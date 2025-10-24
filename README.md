# Homeassistant AWS billing data collector

## What?

This is a system that I developed with heavy assistance from ChatGPT to pull AWS billing data into homeassistant. I wanted to be able to see through the month how much I am being billed. Since I could not find a direct existing way to do this, I set out to develop my own.

## How?

In broad strokes, the process consists of:
- AWS produces a daily report using a Data Export into an S3 bucket setup for the purpose
- A lambda function is used to query those reports and calculate the daily spend, while adding up the value to the monthly total
- An API gateway function connects to the lambda function to facilitate delivering the information to homeassistant
- Authentication is enabled on the API
- homeassistant is setup with a REST template that pulls and parses the information into template sensors
- the template sensors can be used to display the data on HA

## The details

If you want to implement this mechanism, please follow the details in the order listed. Data export, Lambda function API gateway - optionally enable authentication - and homeassistant setup.

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
### AWS Lambda for parsing data exports

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

In order to facilitate calling the lambda function externally and to provide additional flexibility, I used an API gateway. This was an HTTP API with an integration set to the lambda function setup in the previous step `billing-homeassistant-cur`. I have also created a route to map the POST request towards the `/billing` path to the lambda integration. Upon creation, the API is assigned an identifier, which in this guide will be referred to as `API-ID`.

<img alt="API Configuration" src="/img/api_configure.png" width=600px />

<img alt="API Creating route" src="/img/api_configure_route.png" width=600px />

<img alt="API Routes" src="/img/api_routes.png" width=600px />

<img alt="API Integration" src="/img/api_integration.png" width=600px />

<img alt="API information page including URL for accessing" src="/img/api_settings_url.png" width=600px />

Sample curl request that calls the gateway API method externally:

`curl -X POST https://API-ID.execute-api.REGION.amazonaws.com/billing -H "Content-Type: application/json" -d '{"metric": "UnblendedRateCalc"}'`

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
| AWS API Gateway | `billing-homeassistant-api` | HTTP API created to call the lambda function externally |
| AWS API Identifier | `API-ID` | API identifier used in the external URL |

---
### API static key authentication

You will notice that the API request is effectively anonymous. As long as you know the API identifier, you will be able to retrieve the data without having to authenticate.

There are a few layers of security underneath - the API is limited to calling the Lambda function; the Lambda function is limited to reading the reporting S3 bucket; the S3 bucket is private. On the other hand, the Lambda function takes parameters, which opens up a world of exploitation opportunities.

At this point, the AWS side of the project is implemented and functional. Below are sample cURL requests that illustrate how to collect the data after at least one data export exists. You have the choice to continue the project with [homeassistant integration](Homeassistant_REST_Template) or continue through this section and implement authentication.

Authentication and authorisation can be implemented in the API Gateway (and I recommend you do so) as an additional layer to that defense-in-depth. But this is not straight forward. The REST API is more complex to implement and costs more but provides more flexibility. While it is quite feasible and fun to setup a REST API for this purpose, I don't think it is worth the additional effort, since HTTP APIs support custom Lambda, IAM and Cognito authentication. Using a simple Lambda (also provided), it is possible to implement a basic API key type authentication model (this is provided natively in a REST type API). For the purpose of this functionality, using IAM accounts does not seem unreasonable as an alternative to a static API key, however homeassistant must be able to execute the authentication before being able to call the API, since authentication headers will expire and can't realistically be reused for long.

---

The method I developed to implement simple key authentication to the API consisted of setting up a Lambda function and assigning it to an Authorizer within the HTTP API. You can go a step further and have IAM based authentication or Cognito.

A Lambda function which I named `billing-homeassistant-apikey` was created and the environment was set with the API key in a variable as per the code. The code can be found here: [authorizer-apikey.py](/authorizer-apikey.py). This Lambda function does not need additional permissions. For testing, I temporarily created an external URL and tested with cURL as below:

`curl -H 'x-api-key: <KEY>' https://<URL>/`
`curl -H 'x-api-key: bogus' https://<URL>/`

If these 2 requests return success and failure, respectively, the lambda function is working correctly. The public URL can be disabled now since it will never be used.



The HTTP API created above (`billing-homeassistant-api`) was then modified to add an Authorizer object under the POST method for the lambda integration - in my example `/billing`.












AWS Objects created so far:
| Component | Object | Purpose |
| ---      | ------   | -------- |
| AWS Billing and Cost Management<br />Data Export | `billing-homeassistant-export` | Billing Data Export (CUR Legacy) |
| AWS S3 bucket | `billing-homeassistant-temp` | S3 Bucket to store Data Export report data |
| AWS Lambda | `billing-homeassistant-cur` | Lambda function used to parse Data Export report |
| AWS IAM | `billing-homeassistant-cur-policy` | IAM Role created to provide Lambda with access to the S3 bucket |
| AWS API Gateway | `billing-homeassistant-api` | HTTP API created to call the lambda function externally |
| AWS API Identifier | `API-ID` | API identifier used in the external URL |
| AWS Lambda | `billing-homeassistant-apikey` | Lambda function used to implement key authentication to the billing API |

---
### Homeassistant REST template

Now that the AWS side of the project is completed and verified working, the information needs to be pulled by homeassistant. The REST integration was used for this. Firstly the Configuration.yaml file needs an entry for this integration (below), which in my case imports yaml files from a directory called `/config/rest/` on homeassistant.

```
rest: !include_merge_dir rest/
```

Within the directory, I created this yaml file: [aws.yaml](/aws.yaml). It issues the HTTP calls to AWS to pull the data and parses the JSON output into various sensors.

---
### Homeassistant template sensors
