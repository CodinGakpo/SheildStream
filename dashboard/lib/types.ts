// Wire shapes published by the gateway's /ws/dashboard fan-out (backed by
// consumers/alerts/worker.py — see process_message, score_baselines,
// publish_metric_snapshot). Keep in sync with that file's _publish() calls.

export type ThreatDetected = {
  type: "THREAT_DETECTED";
  rule: string;
  severity: "HIGH";
  tenant_id: string;
  endpoint: string;
  source: string;
  count: number;
  timestamp_ms: number;
};

export type BehavioralAnomaly = {
  type: "BEHAVIORAL_ANOMALY";
  severity: "MEDIUM";
  endpoint: string;
  z_score: number;
  current_rps: number;
  count: number;
};

export type MetricSnapshot = {
  type: "METRIC_SNAPSHOT";
  rps: number;
  ts: number;
};

export type AlertMessage = ThreatDetected | BehavioralAnomaly;
export type DashboardMessage = AlertMessage | MetricSnapshot;

export function isAlertMessage(msg: DashboardMessage): msg is AlertMessage {
  return msg.type === "THREAT_DETECTED" || msg.type === "BEHAVIORAL_ANOMALY";
}
