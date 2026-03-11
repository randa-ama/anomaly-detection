# app.py
import io
import json
import os
import boto3
import pandas as pd
import requests
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, Request
from baseline import BaselineManager
from processor import process_file
import logging

LOG_FILE = "anomaly_logging.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Anomaly Detection Pipeline")

s3 = boto3.client("s3")
BUCKET_NAME = os.environ["BUCKET_NAME"]

def sync_logs_to_s3():
    """Helper to push the local log file to S3."""
    try:
        s3.upload_file(LOG_FILE, BUCKET_NAME, f"logs/{LOG_FILE}")
        logger.info("Successfully synced logs to S3.")
    except Exception as e:
        logger.error(f"Failed to sync logs to S3: {e}")

# ── SNS subscription confirmation + message handler ──────────────────────────

@app.post("/notify")
async def handle_sns(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Invalid JSON received in /notify: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    msg_type = request.headers.get("x-amz-sns-message-type")

    # SNS sends a SubscriptionConfirmation before it will deliver any messages.
    # Visiting the SubscribeURL confirms the subscription.
    if msg_type == "SubscriptionConfirmation":
        confirm_url = body["SubscribeURL"]
        requests.get(confirm_url)
        return {"status": "confirmed"}

    if msg_type == "Notification":
        # The SNS message body contains the S3 event as a JSON string
        try:    
            s3_event = json.loads(body["Message"])
            for record in s3_event.get("Records", []):
                key = record["s3"]["object"]["key"]
                
                if key.startswith("raw/") and key.endswith(".csv"):
                    logger.info("New csv added to raw folder, starting processing")
                    background_tasks.add_task(process_file, BUCKET_NAME, key)
                    background_tasks.add_task(sync_logs_to_s3)
                    logger.info(f"Syncing log file to bucket {BUCKET_NAME}")
        except Exception as e:
            logger.error(f"Error parsing S3 event notification: {e}")

    return {"status": "ok"}


# ── Query endpoints ───────────────────────────────────────────────────────────

@app.get("/anomalies/recent")
def get_recent_anomalies(limit: int = 50):
    """Return rows flagged as anomalies across the 10 most recent processed files."""
    try:
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=BUCKET_NAME, Prefix="processed/")
    
        keys = sorted(
            [
                obj["Key"]
                for page in pages
                for obj in page.get("Contents", [])
                if obj["Key"].endswith(".csv")
            ],
            reverse=True,
        )[:10]
    
        all_anomalies = []
        for key in keys:
            response = s3.get_object(Bucket=BUCKET_NAME, Key=key)
            df = pd.read_csv(io.BytesIO(response["Body"].read()))
            if "anomaly" in df.columns:
                flagged = df[df["anomaly"] == True].copy()
                flagged["source_file"] = key
                all_anomalies.append(flagged)
    
        if not all_anomalies:
            return {"count": 0, "anomalies": []}
    
        combined = pd.concat(all_anomalies).head(limit)
        return {"count": len(combined), "anomalies": combined.to_dict(orient="records")}

    except Exception as e:
        logger.error(f"Error in /anomalies/recent: {e}")
        raise HTTPException(status_code=500, detail="S3 Access Error")

@app.get("/anomalies/summary")
def get_anomaly_summary():
    """Aggregate anomaly rates across all processed files using their summary JSONs."""
    try:
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=BUCKET_NAME, Prefix="processed/")
    
        summaries = []
        for page in pages:
            for obj in page.get("Contents", []):
                if obj["Key"].endswith("_summary.json"):
                    response = s3.get_object(Bucket=BUCKET_NAME, Key=obj["Key"])
                    summaries.append(json.loads(response["Body"].read()))
    
        if not summaries:
            return {"message": "No processed files yet."}
    
        total_rows = sum(s["total_rows"] for s in summaries)
        total_anomalies = sum(s["anomaly_count"] for s in summaries)
    
        return {
            "files_processed": len(summaries),
            "total_rows_scored": total_rows,
            "total_anomalies": total_anomalies,
            "overall_anomaly_rate": round(total_anomalies / total_rows, 4) if total_rows > 0 else 0,
            "most_recent": sorted(summaries, key=lambda x: x["processed_at"], reverse=True)[:5],
        }
    except Exception as e:
        logger.error(f"Error in /anomalies/summary: {e}")
        raise HTTPException(status_code=500, detail="Data aggregation error")

@app.get("/baseline/current")
def get_current_baseline():
    """Show the current per-channel statistics the detector is working from."""
    try:
        baseline_mgr = BaselineManager(bucket=BUCKET_NAME)
        baseline = baseline_mgr.load()
    
        channels = {}
        for channel, stats in baseline.items():
            if channel == "last_updated":
                continue
            channels[channel] = {
                "observations": stats["count"],
                "mean": round(stats["mean"], 4),
                "std": round(stats.get("std", 0.0), 4),
                "baseline_mature": stats["count"] >= 30,
            }
    
        return {
            "last_updated": baseline.get("last_updated"),
            "channels": channels,
        }
    except Exception as e:
        logger.error(f'Error in /baseline/current: {e}')
        return {"error": "Could not load baseline from S3"}

@app.get("/health")
def health():
    return {"status": "ok", "bucket": BUCKET_NAME, "timestamp": datetime.utcnow().isoformat()}
