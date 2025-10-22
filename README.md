# Homeassistant AWS billing data collector

## What?

This is a system that I developed with heavy assistance from ChatGPT to pull AWS billing data into homeassistant. I wanted to be able to see through the month how much I am being billed. Since I could not find a direct existing way to do this, I set out to develop my own.

## How?

In broad strokes, the process consists of:
- AWS produces a daily report using a Data Export into an S3 bucket setup for the purpose
- A lambda function is used to query those reports and calculate the daily spend, while adding up the value to the monthly total
- An API gateway function connects to the lambda function to facilitate delivering the information to homeassistant
- homeassistant is setup with a REST template that pulls and parses the information into template sensors
- the template sensors can be used to display the data on HA

## The details

If you want to implement this mechanism, please follow the details in the order listed. Data export, Lambda function API gateway and homeassistant setup.

---
### AWS Billing and Cost Management -> Data Export

This is what AWS provides as the means to round up billing data across the entire account.

<img alt="Data Export details" src="/img/data_export_details.png" width=600px />

The Data Export - named `billing-homeassistant-export` in my case - is set to Legacy CUR Export. The information generated in this Data Export is a long list of items handled by AWS and the impact that their use had on the billing calculation. It should be setup with the following settings:

Data Export Delivery Options
* Report Data Time Granularity: `daily`
* Report Versioning: `Create new report version`
* Compression type: `ZIP`

Data Export Storage Settings
* S3 Bucket: `billing-homeassistant-temp`
* S3 Path Prefix: `/reports` (probably not necessary)

<img alt="Data Export Config 1/2" src="/img/data_export_config_1.png" width=600px />
<img alt="Data Export Config 2/2" src="/img/data_export_config_2.png" width=600px />

When creating the Data Export, an option is provided to create an S3 bucket to store it and suitable permissions will be assigned. My bucket is called `billing-homeassistant-temp`. These permissions are safe and look like this:

```
(...)
            "Sid": "EnableAWSDataExportsToWriteToS3AndCheckPolicy",
            "Effect": "Allow",
            "Principal": {
                "Service": [
                    "billingreports.amazonaws.com",
                    "bcm-data-exports.amazonaws.com"
                ]
            },
            "Action": [
                "s3:PutObject",
                "s3:GetBucketPolicy"
            ],
            "Resource": [
                "arn:aws:s3:::billing-homeassistant-temp",
                "arn:aws:s3:::billing-homeassistant-temp/*"
            ],
            "Condition": {
                "StringLike": {
                    "aws:SourceArn": [
                        "arn:aws:cur:us-east-1:XXXXXXXXXXXX:definition/*",
                        "arn:aws:bcm-data-exports:us-east-1:XXXXXXXXXXXX:export/*"
                    ],
                    "aws:SourceAccount": "XXXXXXXXXXXX"
(...)
```
I don't think the data export can be forced to run, so I waited until it ran once to look at the report on the S3 bucket.

<img alt="Data Export report file sample on S3 bucket" src="/img/data_export_report_file_sample.png" width=600px />

AWS Objects created so far:

| Component | Object | Purpose |
| ---      | ------   | -------- |
| AWS Billing and Cost Management<br />Data Export | `billing-homeassistant-export` | Billing Data Export (CUR Legacy) |
| AWS S3 bucket | `billing-homeassistant-temp` | S3 Bucket to store Data Export report data |

---
### AWS Lambda function

To interpret the Data Exports, there is a Lambda function named `billing-homeassistant-cur` of type Python 3.13 that unpacks and parses the content to run the calculations.
I cannot claim to understand the tables, this was all done by ChatGPT (...a few times until the result was correct). It can be found here [lambda_function.py](/lambda_function.py).

> **NOTICE**: the lambda function was developed to return data via an API gateway, so the output is formatted for that purpose, not necessarily to be consumed directly.

The function call requires one parameter named `metric` which can be one of several strings:
- `unblendedcost`
- `UnblendedRateCalc` - the setting that works best for me
- `pricing/publicOnDemandCost`
- `AmortizedCost`
- `BlendedCost`


I cannot explain the intricacies of AWS billing that tell these options apart. But I know that I use `UnblendedRateCalc` which provides daily and cumulative monthly cost.

The Lambda function requires privileges to read from the S3 bucket `billing-homeassistant-temp`, which I assigned via a role similar to the below:

```
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "VisualEditor0",
            "Effect": "Allow",
            "Action": "cur:DescribeReportDefinitions",
            "Resource": "*"
        },
        {
            "Sid": "VisualEditor1",
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:GetObjectAcl",
                "s3:GetObject",
                "s3:ListBucket",
                "s3:DeleteObject",
                "s3:PutObjectAcl"
            ],
            "Resource": [
                "arn:aws:s3:::billing-homeassistant-temp",
                "arn:aws:s3:::billing-homeassistant-temp/*"
```

<img alt="Lambda Function permissions including custom role" src="/img/lambda-permissions.png" width=600px />

Successfully calling the lambda function should return this kind of content (beautiful it is not, remember this is to be served via an API gateway route):

```
{
  "statusCode": 200,
  "body": "{\"total_spend\": 2.45, \"last_day_spend\": 0.116, \"latest_day\": \"2025-10-21\", \"metric_used\": \"UnblendedRateCalc\", \"latest_report\": \"reports/billing-homeassistant-export/20251001-20251101/20251022T171233Z/billing-homeassistant-export-00001.csv.zip\", \"report_timestamp\": \"20251022T171233Z\", \"old_reports_deleted\": [], \"message\": \"Processed latest CUR report successfully\"}",
  "headers": {
    "Content-Type": "application/json"
  }
}
```

AWS Objects created so far:
| Component | Object | Purpose |
| ---      | ------   | -------- |
| AWS Billing and Cost Management<br />Data Export | `billing-homeassistant-export` | Billing Data Export (CUR Legacy) |
| AWS S3 bucket | `billing-homeassistant-temp` | S3 Bucket to store Data Export report data |
| AWS Lambda | `billing-homeassistant-cur` | Lambda function used to parse Data Export report |
| AWS IAM | `billing-homeassistant-cur-policy` | IAM Role created to provide Lambda with access to the S3 bucket |

---
### AWS API Gateway

Sample curl request that calls the gateway API method externally:

`curl -X POST https://<API-ID>.execute-api.<REGION>.amazonaws.com/billing -H "Content-Type: application/json" -d '{"metric": "UnblendedRateCalc"}'`

```
{
  "total_spend": 2.45,
  "last_day_spend": 0.116,
  "latest_day": "2025-10-21",
  "metric_used": "UnblendedRateCalc",
  "latest_report": "reports/billing-homeassistant-export/20251001-20251101/20251022T171233Z/billing-homeassistant-export-00001.csv.zip",
  "report_timestamp": "20251022T171233Z",
  "old_reports_deleted": [],
  "message": "Processed latest CUR report successfully"
}
```

That looks much more usable than the lambda output!

AWS Objects created so far:
| Component | Object | Purpose |
| ---      | ------   | -------- |
| AWS Billing and Cost Management<br />Data Export | `billing-homeassistant-export` | Billing Data Export (CUR Legacy) |
| AWS S3 bucket | `billing-homeassistant-temp` | S3 Bucket to store Data Export report data |
| AWS Lambda | `billing-homeassistant-cur` | Lambda function used to parse Data Export report |
| AWS IAM | `billing-homeassistant-cur-policy` | IAM Role created to provide Lambda with access to the S3 bucket |
| AWS API Gateway | `ha-api` | API Gateway routed method to call the lambda function externally |

---
### Homeassistant REST template

Configuration.yaml
```
rest: !include_merge_dir rest/
```

[aws.yaml](/aws.yaml)

---
### Homeassistant template sensors
