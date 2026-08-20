"""DuckDB-asosidagi ma'lumot qatlami. CSV fayllarga to'g'ridan-to'g'ri SQL
so'rov yuboradi -- alohida DB serveri yoki oldindan yuklash kerak emas.
Har bir panel shu yerdagi `DataStore`dan foydalanadi."""

import os

import duckdb

# dataset nomi -> fayl nomi (dataset_builder.py'ning haqiqiy chiqishi bilan mos)
DATASET_FILES = {
    "transport_events": "transport_events.csv",
    "flow_stats": "flow_stats.csv",
    "port_stats": "port_stats.csv",
    "rtt": "rtt.csv",
    "traffic_log": "traffic_log.csv",
    "impairments": "impairments.csv",
    "path_traces": "path_traces.csv",
    "hop_details": "hop_details.csv",
    "dns_queries": "dns_queries.csv",
    "http_transactions": "http_transactions.csv",
    "anomaly_events": "anomaly_events.csv",
    "connection_states": "connection_states.csv",
    "nat_translations": "nat_translations.csv",
}


class DataStore:
    """Bitta dataset papkasini (masalan results/combined yoki
    results/five_as_ospf/datasets) DuckDB view'lari sifatida ochadi."""

    def __init__(self, data_dir):
        self.con = duckdb.connect(":memory:")
        self.data_dir = None
        self.available = []  # shu papkada haqiqatda mavjud dataset nomlari
        self.errors = {}     # o'qib bo'lmagan fayllar: name -> xato matni
        self.set_data_dir(data_dir)

    def set_data_dir(self, data_dir):
        """Papkani (qayta) ochadi, mavjud CSV'lar uchun VIEW yaratadi.

        Har bir VIEW alohida try/except ichida yaratiladi -- bitta buzuq/yarim
        yozilgan CSV (masalan to'xtatilgan simulyatsiyadan) butun ilovani
        yiqitmasin, faqat o'sha dataset o'tkazib yuboriladi va `self.errors`ga
        yoziladi. Papka almashtirilganda eski VIEW'lar ham tozalanadi -- aks
        holda yangi papkada yo'q dataset uchun eski papkaning ma'lumoti
        ko'rinib qolardi."""
        self.data_dir = data_dir
        # Eskilarini tashla -- almashtirilgan papkada yo'q dataset stale
        # VIEW sifatida qolib ketmasligi uchun.
        for name in DATASET_FILES:
            try:
                self.con.execute(f"DROP VIEW IF EXISTS {name}")
            except Exception:
                pass
        self.available = []
        self.errors = {}
        for name, filename in DATASET_FILES.items():
            path = os.path.join(data_dir, filename)
            if not os.path.exists(path):
                continue
            escaped = path.replace("'", "''")
            try:
                self.con.execute(
                    f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_csv_auto('{escaped}')"
                )
            except Exception as e:
                self.errors[name] = str(e)
                continue
            self.available.append(name)

    def has(self, name):
        return name in self.available

    def query_df(self, sql):
        """SQL -> pandas.DataFrame. Bo'sh natija ham DataFrame (ustunlar bilan)."""
        return self.con.execute(sql).fetchdf()

    def row_counts(self):
        counts = {}
        for name in list(self.available):
            try:
                counts[name] = self.con.execute(
                    f"SELECT count(*) FROM {name}").fetchone()[0]
            except Exception as e:
                # VIEW yaratilganda o'tgan, lekin so'rov paytida buzilgan CSV --
                # xatoni yig'ib, sanoqdan chiqarib tashlaymiz (ilova ochiq qolsin).
                self.errors[name] = str(e)
        return counts

    def routing_modes(self):
        """Ma'lumotda uchraydigan barcha `routing` qiymatlari (bo'lsa)."""
        if not self.available:
            return []
        for name in ("path_traces", "traffic_log", "transport_events"):
            if self.has(name):
                rows = self.con.execute(
                    f"SELECT DISTINCT routing FROM {name} WHERE routing IS NOT NULL ORDER BY 1"
                ).fetchall()
                return [r[0] for r in rows]
        return []
