"""
Yig'ilgan ma'lumotlardan ML-tayyor dataset yaratish.
PCAP -> flow features, ONOS stats -> CSV, hammasi -> Parquet.
"""

import csv
import glob
import json
import os
import time

import pandas as pd
import numpy as np

from realistic_internet.config import (
    PCAP_DIR, STATS_DIR, FLOWS_DIR, ONOS_STATS_DIR,
    SFLOW_DATA_DIR, DATASET_DIR, AS_DEFINITIONS,
)


class DatasetBuilder:
    """Barcha yig'ilgan datalarni ML dataset'ga aylantirish."""

    def __init__(self):
        os.makedirs(DATASET_DIR, exist_ok=True)
        self._metadata_path = os.path.join(STATS_DIR, "topology_metadata.json")

    def build_all(self):
        """Barcha datasetlarni yaratish."""
        print("\n[Dataset] ML dataset yaratilmoqda...")

        dfs = {}

        # 1. ONOS flow stats
        df = self._build_flow_stats()
        if df is not None and not df.empty:
            dfs["onos_flows"] = df
            print(f"  Flow stats:    {len(df)} yozuv")

        # 2. ONOS port stats
        df = self._build_port_stats()
        if df is not None and not df.empty:
            dfs["onos_ports"] = df
            print(f"  Port stats:    {len(df)} yozuv")

        # 3. RTT measurements
        df = self._build_rtt_data()
        if df is not None and not df.empty:
            dfs["rtt"] = df
            print(f"  RTT data:      {len(df)} yozuv")

        # 4. Impairment events
        df = self._build_impairment_data()
        if df is not None and not df.empty:
            dfs["impairments"] = df
            print(f"  Impairments:   {len(df)} yozuv")

        # 5. Traffic events
        df = self._build_traffic_data()
        if df is not None and not df.empty:
            dfs["traffic"] = df
            print(f"  Traffic events: {len(df)} yozuv")

        # 6. Topology events
        df = self._build_topology_events()
        if df is not None and not df.empty:
            dfs["topology"] = df
            print(f"  Topology events: {len(df)} yozuv")

        # 7. PCAP -> nfstream flow features
        df = self._build_pcap_features()
        if df is not None and not df.empty:
            dfs["pcap_flows"] = df
            print(f"  PCAP flows:    {len(df)} yozuv")

        # Saqlash
        for name, df in dfs.items():
            parquet_path = os.path.join(DATASET_DIR, f"{name}.parquet")
            csv_path = os.path.join(DATASET_DIR, f"{name}.csv")
            df.to_parquet(parquet_path, index=False)
            df.to_csv(csv_path, index=False)

        # Birlashtrilgan dataset
        if dfs:
            self._build_combined_dataset(dfs)

        self._print_summary(dfs)

    def _build_flow_stats(self) -> pd.DataFrame:
        """ONOS flow stats JSONL -> DataFrame."""
        path = os.path.join(ONOS_STATS_DIR, "flow_stats.jsonl")
        return self._jsonl_to_df(path)

    def _build_port_stats(self) -> pd.DataFrame:
        """ONOS port stats JSONL -> DataFrame."""
        path = os.path.join(ONOS_STATS_DIR, "port_stats.jsonl")
        df = self._jsonl_to_df(path)
        if df is None or df.empty:
            return df

        # Computed features qo'shish
        if "bytes_rx" in df.columns and "bytes_tx" in df.columns:
            df["total_bytes"] = df["bytes_rx"] + df["bytes_tx"]
            df["total_packets"] = df.get("packets_rx", 0) + df.get("packets_tx", 0)
            df["total_dropped"] = (
                df.get("packets_rx_dropped", 0) + df.get("packets_tx_dropped", 0)
            )
            df["total_errors"] = (
                df.get("packets_rx_errors", 0) + df.get("packets_tx_errors", 0)
            )

            # Utilization (bytes per second)
            df = df.sort_values(["device_id", "port", "ts"])
            for col in ["bytes_rx", "bytes_tx", "total_bytes"]:
                df[f"{col}_rate"] = df.groupby(["device_id", "port"])[col].diff()
                ts_diff = df.groupby(["device_id", "port"])["ts"].diff()
                df[f"{col}_rate"] = df[f"{col}_rate"] / ts_diff.clip(lower=0.1)

        return df

    def _build_rtt_data(self) -> pd.DataFrame:
        """RTT measurements JSONL -> DataFrame."""
        path = os.path.join(STATS_DIR, "rtt_measurements.jsonl")
        df = self._jsonl_to_df(path)
        if df is None or df.empty:
            return df

        # AS ma'lumoti qo'shish
        metadata = self._load_metadata()
        if metadata:
            host_to_as = metadata.get("host_to_as", {})
            df["src_as"] = df["src"].map(host_to_as)
            df["dst_as"] = df["dst"].map(host_to_as)
            df["same_as"] = df["src_as"] == df["dst_as"]
            # AS orasidagi hop soni (taxminiy)
            df["cross_as"] = (~df["same_as"]).astype(int)

        return df

    def _build_impairment_data(self) -> pd.DataFrame:
        """Impairment events JSONL -> DataFrame."""
        path = os.path.join(STATS_DIR, "impairment_events.jsonl")
        return self._jsonl_to_df(path)

    def _build_traffic_data(self) -> pd.DataFrame:
        """Traffic events JSONL -> DataFrame."""
        path = os.path.join(STATS_DIR, "traffic_events.jsonl")
        return self._jsonl_to_df(path)

    def _build_topology_events(self) -> pd.DataFrame:
        """Topology events JSONL -> DataFrame."""
        path = os.path.join(ONOS_STATS_DIR, "topology_events.jsonl")
        return self._jsonl_to_df(path)

    def _build_pcap_features(self) -> pd.DataFrame:
        """PCAP fayllardan flow feature extraction (nfstream)."""
        pcap_files = glob.glob(os.path.join(PCAP_DIR, "*.pcap"))
        if not pcap_files:
            return None

        all_flows = []

        try:
            from nfstream import NFStreamer
            for pcap_path in pcap_files:
                try:
                    streamer = NFStreamer(source=pcap_path, statistical_analysis=True)
                    for flow in streamer:
                        flow_dict = flow.to_dict() if hasattr(flow, "to_dict") else {}
                        if not flow_dict:
                            # Manual extraction
                            flow_dict = {
                                "src_ip": flow.src_ip,
                                "dst_ip": flow.dst_ip,
                                "src_port": flow.src_port,
                                "dst_port": flow.dst_port,
                                "protocol": flow.protocol,
                                "bidirectional_packets": flow.bidirectional_packets,
                                "bidirectional_bytes": flow.bidirectional_bytes,
                                "bidirectional_duration_ms": flow.bidirectional_duration_ms,
                                "src2dst_packets": flow.src2dst_packets,
                                "src2dst_bytes": flow.src2dst_bytes,
                                "dst2src_packets": flow.dst2src_packets,
                                "dst2src_bytes": flow.dst2src_bytes,
                            }
                        flow_dict["pcap_source"] = os.path.basename(pcap_path)
                        all_flows.append(flow_dict)
                except Exception:
                    pass
        except ImportError:
            # nfstream yo'q bo'lsa, tshark bilan
            print("  [nfstream topilmadi, tshark ishlatilmoqda]")
            for pcap_path in pcap_files:
                try:
                    flows = self._extract_with_tshark(pcap_path)
                    all_flows.extend(flows)
                except Exception:
                    pass

        if all_flows:
            return pd.DataFrame(all_flows)
        return None

    def _extract_with_tshark(self, pcap_path: str) -> list:
        """tshark bilan asosiy flow ma'lumotlarini chiqarish."""
        import subprocess
        try:
            result = subprocess.run(
                [
                    "tshark", "-r", pcap_path, "-q",
                    "-z", "conv,tcp",
                ],
                capture_output=True, text=True, timeout=60,
            )
            # Parse tshark conversation output
            flows = []
            in_table = False
            for line in result.stdout.split("\n"):
                if "<=>" in line:
                    parts = line.split()
                    if len(parts) >= 10:
                        flows.append({
                            "src": parts[0],
                            "dst": parts[2],
                            "frames_a_to_b": int(parts[3]) if parts[3].isdigit() else 0,
                            "bytes_a_to_b": int(parts[4]) if parts[4].isdigit() else 0,
                            "frames_b_to_a": int(parts[5]) if parts[5].isdigit() else 0,
                            "bytes_b_to_a": int(parts[6]) if parts[6].isdigit() else 0,
                            "pcap_source": os.path.basename(pcap_path),
                        })
            return flows
        except Exception:
            return []

    def _build_combined_dataset(self, dfs: dict):
        """Asosiy birlashtrilgan dataset yaratish."""
        # Port stats + RTT = eng muhim ML dataset
        combined_rows = []

        if "onos_ports" in dfs and "rtt" in dfs:
            port_df = dfs["onos_ports"]
            rtt_df = dfs["rtt"]

            # Har bir vaqt oynasi uchun aggregated features
            if "ts" in port_df.columns:
                port_df["time_bucket"] = (port_df["ts"] // 10).astype(int)

                for bucket, group in port_df.groupby("time_bucket"):
                    row = {
                        "time_bucket": bucket,
                        "timestamp": bucket * 10,
                        # Port stats aggregation
                        "total_bytes_rx": group["bytes_rx"].sum() if "bytes_rx" in group else 0,
                        "total_bytes_tx": group["bytes_tx"].sum() if "bytes_tx" in group else 0,
                        "total_packets_rx": group.get("packets_rx", pd.Series([0])).sum(),
                        "total_packets_tx": group.get("packets_tx", pd.Series([0])).sum(),
                        "total_dropped": group.get("total_dropped", pd.Series([0])).sum(),
                        "total_errors": group.get("total_errors", pd.Series([0])).sum(),
                        "active_ports": len(group),
                        "active_devices": group["device_id"].nunique() if "device_id" in group else 0,
                    }

                    # Shu vaqt oynasidagi RTT
                    ts_start = bucket * 10
                    ts_end = ts_start + 10
                    rtt_window = rtt_df[
                        (rtt_df["ts"] >= ts_start) & (rtt_df["ts"] < ts_end)
                    ]
                    if not rtt_window.empty:
                        row["avg_rtt"] = rtt_window["rtt_avg"].mean()
                        row["max_rtt"] = rtt_window["rtt_max"].max()
                        row["min_rtt"] = rtt_window["rtt_min"].min()
                        row["avg_loss"] = rtt_window["packet_loss_pct"].mean()

                    combined_rows.append(row)

        if combined_rows:
            combined_df = pd.DataFrame(combined_rows)
            combined_df.to_parquet(
                os.path.join(DATASET_DIR, "combined_network_state.parquet"),
                index=False,
            )
            combined_df.to_csv(
                os.path.join(DATASET_DIR, "combined_network_state.csv"),
                index=False,
            )
            print(f"  Combined dataset: {len(combined_df)} yozuv")

    def _load_metadata(self) -> dict:
        """Topologiya metadata'ni yuklash."""
        try:
            with open(self._metadata_path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _jsonl_to_df(path: str) -> pd.DataFrame:
        """JSONL faylni DataFrame ga o'girish."""
        if not os.path.exists(path):
            return None
        rows = []
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            return None
        if rows:
            return pd.DataFrame(rows)
        return None

    def _print_summary(self, dfs: dict):
        """Yakuniy xulosa."""
        print("\n" + "=" * 60)
        print("DATASET XULOSA")
        print("=" * 60)

        total_rows = 0
        for name, df in dfs.items():
            rows = len(df)
            cols = len(df.columns)
            total_rows += rows
            print(f"  {name:25s}: {rows:>8,} rows x {cols:>3} columns")

        print(f"\n  Jami: {total_rows:,} yozuv")
        print(f"  Saqlangan: {DATASET_DIR}/")
        print(f"  Formatlar: .parquet + .csv")

        # Fayl o'lchamlari
        total_size = 0
        for f in glob.glob(os.path.join(DATASET_DIR, "*")):
            size = os.path.getsize(f)
            total_size += size

        print(f"  Umumiy hajm: {total_size / (1024*1024):.1f} MB")
        print("=" * 60 + "\n")
