import boto3
import csv
import io
import zipfile
import json as json_module
from datetime import datetime

s3 = boto3.client("s3")

# ------------------------------
# CONFIGURATION
# ------------------------------
bucket = "billing-homeassistant-temp"
prefix = "reports/billing-homeassistant-export/"
delete_old_reports = True  # set False to keep all old reports


# ------------------------------
# METRIC SELECTION LOGIC
# ------------------------------
def get_metric_value(row, metric_name):
    try:
        if metric_name == "unblendedcost":
            return float(row.get("lineItem/UnblendedCost", 0) or 0)
        elif metric_name == "UnblendedRateCalc":
            usage = float(row.get("lineItem/UsageAmount", 0) or 0)
            rate = float(row.get("lineItem/UnblendedRate", 0) or 0)
            return usage * rate
        elif metric_name == "pricing/publicOnDemandCost":
            return float(row.get("pricing/publicOnDemandCost", 0) or 0)
        elif metric_name == "AmortizedCost":
            return float(row.get("lineItem/AmortizedCost", 0) or 0)
        elif metric_name == "BlendedCost":
            return float(row.get("lineItem/BlendedCost", 0) or 0)
        else:
            return 0.0
    except Exception:
        return 0.0


# ------------------------------
# MAIN HANDLER
# ------------------------------
def lambda_handler(event, context):
    # 1. Metric selection from request
    metric_used = "unblendedcost"  # default
    if isinstance(event, dict):
        if "metric" in event:
            metric_used = str(event["metric"])
        elif "body" in event:  # API Gateway case
            import json
            try:
                body = json.loads(event["body"])
                metric_used = body.get("metric", metric_used)
            except Exception:
                pass

    # 2. Find latest monthly directory (e.g., reports/.../20250901-20251001/)
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/")
    if "CommonPrefixes" not in resp:
        return {"statusCode": 404, "body": {"error": "No monthly directories found"}}

    latest_month_dir = sorted(cp["Prefix"] for cp in resp["CommonPrefixes"])[-1]

    # 3. Find latest timestamped directory inside the month
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=latest_month_dir, Delimiter="/")
    if "CommonPrefixes" not in resp:
        return {"statusCode": 404, "body": {"error": "No report subdirectories found"}}

    latest_report_dir = sorted(cp["Prefix"] for cp in resp["CommonPrefixes"])[-1]
    report_timestamp = latest_report_dir.strip("/").split("/")[-1]

    # 4. Find the .zip file in that directory
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=latest_report_dir)
    zip_files = [obj["Key"] for obj in resp.get("Contents", []) if obj["Key"].endswith(".zip")]
    if not zip_files:
        return {"statusCode": 404, "body": {"error": "No CUR zip files found"}}

    latest_zip = sorted(zip_files)[-1]

    # 5. Download and unzip CSV
    zip_obj = s3.get_object(Bucket=bucket, Key=latest_zip)
    with zipfile.ZipFile(io.BytesIO(zip_obj["Body"].read())) as z:
        csv_filename = z.namelist()[0]
        with z.open(csv_filename) as f:
            reader = csv.DictReader(io.TextIOWrapper(f))

            total_spend = 0.0
            daily_totals = {}

            for row in reader:
                val = get_metric_value(row, metric_used)
                total_spend += val

                usage_start = row.get("lineItem/UsageStartDate")
                if usage_start:
                    day = usage_start.split("T")[0]
                    daily_totals[day] = daily_totals.get(day, 0.0) + val

    # 6. Pick last complete day (yesterday in CUR terms)
    last_day_spend = 0.0
    latest_day = None

    if daily_totals:
        sorted_days = sorted(daily_totals.keys())
        if len(sorted_days) >= 2:
            # Use the second-to-last day (yesterday)
            latest_day = sorted_days[-2]
        else:
            # Only one day available
            latest_day = sorted_days[-1]
        last_day_spend = daily_totals.get(latest_day, 0.0)

    # 7. Optionally delete old report dirs (keep only latest)
    old_reports_deleted = []
    if delete_old_reports:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=latest_month_dir, Delimiter="/")
        all_dirs = sorted(cp["Prefix"] for cp in resp.get("CommonPrefixes", []))
        for d in all_dirs[:-1]:  # delete all except latest
            del_resp = s3.list_objects_v2(Bucket=bucket, Prefix=d)
            for obj in del_resp.get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=obj["Key"])
            old_reports_deleted.append(d)

    # OUTPUT FORMATTED FOR USE BY API GATEWAY!
    # 8. Return JSON output with 2 decimal precision
    return {
        "statusCode": 200,
        "body": json_module.dumps({
            "total_spend": round(total_spend, 2),
            "last_day_spend": round(last_day_spend, 3),
            "latest_day": latest_day,
            "metric_used": metric_used,
            "latest_report": latest_zip,
            "report_timestamp": report_timestamp,
            "old_reports_deleted": old_reports_deleted,
            "message": "Processed latest CUR report successfully",
        }),
        "headers": {
            "Content-Type": "application/json"
        }
    }
