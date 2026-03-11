#!/usr/bin/env python3
import json
import math
import boto3
import logging
from datetime import datetime
from typing import Optional

s3 = boto3.client("s3")

logger = logging.getLogger(__name__)
class BaselineManager:
    """
    Maintains a per-channel running baseline using Welford's online algorithm,
    which computes mean and variance incrementally without storing all past data.
    """

    def __init__(self, bucket: str, baseline_key: str = "state/baseline.json"):
        self.bucket = bucket
        self.baseline_key = baseline_key

    def load(self) -> dict:
        try:
            response = s3.get_object(Bucket=self.bucket, Key=self.baseline_key)
            return json.loads(response["Body"].read())
        except s3.exceptions.NoSuchKey:
            logger.info('No existing baseline found in S3, starting fresh')
            return {}
        except Exception as e:
            logger.error(f'Error loading baseline {self.baseline_key} from S3: {e}')
            return {}
            

    def save(self, baseline: dict):
        try:
            baseline["last_updated"] = datetime.utcnow().isoformat()
            s3.put_object(
                Bucket=self.bucket,
                Key=self.baseline_key,
                Body=json.dumps(baseline, indent=2),
                ContentType="application/json"
            )
            logger.info(f'Successfully uploaded new baseline {self.baseline_key} to S3 bucket {self.bucket}')
        except Exception as e:
            logger.error(f'Error saving baseline to S3 bucket {self.bucket}: {e}')

    def update(self, baseline: dict, channel: str, new_values: list[float]) -> dict:
        """
        Welford's online algorithm for numerically stable mean and variance.
        Each channel tracks: count, mean, M2 (sum of squared deviations).
        Variance = M2 / count, std = sqrt(variance).
        """
        try:
            if channel not in baseline:
                baseline[channel] = {"count": 0, "mean": 0.0, "M2": 0.0}
                logger.info(f"Successfully added channel {channel} to baseline ")
    
            state = baseline[channel]
    
            for value in new_values:
                state["count"] += 1
                delta = value - state["mean"]
                state["mean"] += delta / state["count"]
                delta2 = value - state["mean"]
                state["M2"] += delta * delta2
    
            # Only compute std once we have enough observations
            if state["count"] >= 2:
                variance = state["M2"] / state["count"]
                state["std"] = math.sqrt(variance)
            else:
                state["std"] = 0.0
    
            baseline[channel] = state
            return baseline

        except Exception as e:
            logger.error(f'Error updating basline for channel {channel}: {e}, keeping previous baseline')
            return baseline

    def get_stats(self, baseline: dict, channel: str) -> Optional[dict]:
        return baseline.get(channel)
