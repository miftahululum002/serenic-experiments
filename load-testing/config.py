"""Konfigurasi harness, dibaca dari .env / environment."""
import hashlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parent

# Dua lapis konfigurasi:
#   .env            -> milik bersama: kredensial, template payload, output
#   .env.<profile>  -> khusus target: URL, Redis, antrean, jumlah replika
#
# Profil dipilih dengan salah satu dari, berurutan:
#   1. env var shell PROFILE   -> PROFILE=prod python tests/t1_service_time.py
#   2. APP_MODE di .env        -> APP_MODE=dev, lalu cukup `python tests/...`
#
# Shell menang atas .env supaya override sekali jalan selalu bisa. Arah itu
# juga yang aman: APP_MODE=dev yang terlupakan hanya membuat perintah mengenai
# staging, sedangkan PROFILE=prod yang diketik eksplisit selalu dihormati.
#
# Lapisan profil menimpa .env, jadi kredensial tidak perlu diduplikasi.
#
# Prioritas nilai, dari yang menang:
#   1. env var shell   -> override sekali jalan, mis. WORKER_REPLICAS=2 ...
#   2. .env.<profile>
#   3. .env

# load_dotenv(override=True) juga menimpa env var shell yang sudah di-set, jadi
# nilai shell disimpan dulu lalu dikembalikan setelah semua file dimuat.
_SHELL_ENV = dict(os.environ)
_SHELL_PROFILE = os.getenv("PROFILE", "").strip().lower()

ACTIVE_PROFILE = _SHELL_PROFILE
PROFILE_SOURCE = "PROFILE" if _SHELL_PROFILE else ""

if load_dotenv is not None:
    load_dotenv(ROOT / ".env")

    if not ACTIVE_PROFILE:
        for var in ("APP_MODE", "PROFILE"):
            val = os.getenv(var, "").strip().lower()
            if val:
                ACTIVE_PROFILE, PROFILE_SOURCE = val, f"{var} di .env"
                break

    if ACTIVE_PROFILE:
        pf = ROOT / f".env.{ACTIVE_PROFILE}"
        if not pf.exists():
            available = sorted(p.name.removeprefix(".env.")
                               for p in ROOT.glob(".env.*") if p.suffix != ".example")
            sys.exit(f"[config] profil '{ACTIVE_PROFILE}' (dari {PROFILE_SOURCE}) tidak ada "
                     f"— berkas {pf.name} tidak ditemukan.\n"
                     f"         Tersedia: {', '.join(available) or '(belum ada)'}")
        load_dotenv(pf, override=True)
    elif any(ROOT.glob(".env.dev")) or any(ROOT.glob(".env.prod")):
        print("[config] Profil tidak dipilih — hanya memakai .env.\n"
              "         Isi APP_MODE=dev di .env, atau jalankan dengan PROFILE=dev di depan "
              "perintah.\n")

    os.environ.update(_SHELL_ENV)  # kembalikan prioritas env var shell


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "y")


def _path(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / p).resolve()


@dataclass
class Settings:
    api_base_url: str = os.getenv("API_BASE_URL", "").rstrip("/")
    api_v2_prefix: str = os.getenv("API_V2_PREFIX", "/integrations/v2")
    api_key: str = os.getenv("API_KEY", "")
    # gateway = lewat Kong, kirim header apiKey (jalur normal)
    # direct   = langsung ke container FastAPI, kirim X-Consumer-Custom-ID
    #            (dipakai kalau menembak api-server_LOADTEST yang mem-bypass gateway)
    auth_mode: str = os.getenv("AUTH_MODE", "gateway").strip().lower()
    consumer_id: str = os.getenv("CONSUMER_ID", "")
    org_label: str = os.getenv("ORG_LABEL", "")
    # Dipakai sebagai nama folder arsip payload. Kalau kosong, jatuh ke
    # ORG_LABEL lalu CONSUMER_ID.
    orgid: str = os.getenv("ORGID", "")
    basic_auth_user: str = os.getenv("API_BASIC_AUTH_USER", "")
    basic_auth_pass: str = os.getenv("API_BASIC_AUTH_PASS", "")
    allow_prod: bool = _bool("ALLOW_PROD")

    redis_url: str = os.getenv("REDIS_URL", "")
    parsing_queue: str = os.getenv("PARSING_QUEUE", "")
    # Jumlah replika emr-integration-data-parsing-worker_PROD. Satu container RQ
    # mengerjakan satu job pada satu waktu, jadi angka ini = konkurensi parsing.
    worker_replicas: int = int(os.getenv("WORKER_REPLICAS", "4"))
    analysis_coordinator_queue: str = os.getenv("ANALYSIS_COORDINATOR_QUEUE", "")
    analysis_queue: str = os.getenv("ANALYSIS_QUEUE", "")

    template_payload: Path = field(default_factory=lambda: _path("TEMPLATE_PAYLOAD", "../../serenic_api_service/request-payload.json"))
    synthetic_prefix: str = os.getenv("SYNTHETIC_PREFIX", "LT")
    location_id: str = os.getenv("LOCATION_ID", "")
    dpjp_id: str = os.getenv("DPJP_ID", "")

    results_dir: Path = field(default_factory=lambda: _path("RESULTS_DIR", "results"))

    # Database — hanya dipakai tools/cleanup_encounters.py.
    db_host: str = os.getenv("DB_HOST", "")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "")
    db_user: str = os.getenv("DB_USER", "postgres")
    db_password: str = os.getenv("DB_PASSWORD", "")

    # Arsip request dan response.
    save_payloads: bool = _bool("SAVE_PAYLOADS", True)
    payload_dir: Path = field(default_factory=lambda: _path("PAYLOAD_DIR", "payload"))
    payload_max_mb: float = float(os.getenv("PAYLOAD_MAX_MB", "2000"))
    save_responses: bool = _bool("SAVE_RESPONSES", True)
    response_dir: Path = field(default_factory=lambda: _path("RESPONSE_DIR", "response"))
    response_max_mb: float = float(os.getenv("RESPONSE_MAX_MB", "500"))

    @property
    def payload_org_dir(self) -> str:
        return self.orgid or self.org_label or self.consumer_id or "unknown-org"

    @property
    def organization_id(self) -> str:
        """managing_organization untuk query database."""
        return os.getenv("DB_ORGANIZATION_ID", "") or self.orgid or self.org_label or self.consumer_id

    def require_db(self) -> None:
        missing = [n for n, v in (("DB_HOST", self.db_host), ("DB_NAME", self.db_name),
                                  ("DB_PASSWORD", self.db_password)) if not v]
        if missing:
            sys.exit(f"[config] belum di-set: {', '.join(missing)}")
        if not self.organization_id:
            sys.exit("[config] organisasi tidak diketahui — isi DB_ORGANIZATION_ID, ORGID, "
                     "atau ORG_LABEL.\n"
                     "         Penghapusan selalu dibatasi satu managing_organization.")

    # --- URL helpers -------------------------------------------------------
    @property
    def url_new(self) -> str:
        return f"{self.api_base_url}{self.api_v2_prefix}/encounters/new"

    @property
    def url_update(self) -> str:
        return f"{self.api_base_url}{self.api_v2_prefix}/encounters/update"

    @property
    def url_health(self) -> str:
        return f"{self.api_base_url}{self.api_v2_prefix}/health_check"

    @property
    def url_prerequisites(self) -> str:
        return f"{self.api_base_url}{self.api_v2_prefix}/prerequisites"

    @property
    def headers(self) -> dict:
        base = {"Content-Type": "application/json"}
        if self.auth_mode == "direct":
            # Bypass gateway: FastAPI mewajibkan header ini secara langsung.
            base["X-Consumer-Custom-ID"] = self.consumer_id
        else:
            base["apiKey"] = self.api_key
        return base

    @property
    def auth(self):
        if self.basic_auth_user:
            return (self.basic_auth_user, self.basic_auth_pass)
        return None

    # --- Validasi ----------------------------------------------------------
    @property
    def is_direct(self) -> bool:
        return self.auth_mode == "direct"

    @property
    def credential_fingerprint(self) -> str:
        """Identitas kredensial yang aman dicetak.

        Kunci dev dan prod berbeda; sidik jari ini membuat 'salah kunci untuk
        target ini' terlihat sebelum request pertama dikirim, tanpa membocorkan
        nilainya ke terminal atau log.
        """
        if self.is_direct:
            return f"X-Consumer-Custom-ID={self.consumer_id or '(kosong)'}"
        if not self.api_key:
            return "apiKey=(kosong)"
        if "<ISI" in self.api_key:
            return "apiKey=(masih placeholder)"
        digest = hashlib.sha256(self.api_key.encode()).hexdigest()[:8]
        return f"apiKey sha256:{digest} (panjang {len(self.api_key)})"

    def require_api(self) -> None:
        need = [("API_BASE_URL", self.api_base_url)]
        need.append(("CONSUMER_ID", self.consumer_id) if self.is_direct else ("API_KEY", self.api_key))
        missing = [n for n, v in need if not v]
        if missing:
            sys.exit(f"[config] belum di-set: {', '.join(missing)} (lihat .env.example)")

        # Placeholder di file profil tidak boleh lolos diam-diam.
        placeholders = [n for n, v in [*need, ("REDIS_URL", self.redis_url)] if "<ISI" in str(v)]
        if placeholders:
            sys.exit(f"[config] masih berisi placeholder: {', '.join(placeholders)}\n"
                     f"         Isi nilainya di .env.{ACTIVE_PROFILE or '<profil>'}")

        # Lewat SSH tunnel host-nya 127.0.0.1, jadi nama host saja tidak cukup
        # untuk mengenali target produksi — nama profil ikut dipakai.
        host = self.api_base_url.split("//")[-1].split("/")[0]
        if (host.startswith("api.serenic.ai") or ACTIVE_PROFILE == "prod") and not self.allow_prod:
            sys.exit(
                "[config] API_BASE_URL menunjuk ke PRODUCTION.\n"
                "         Test ini menulis encounter sintetis ke database produksi, dan\n"
                "         (kalau lewat api-server_PROD) memicu analisis LLM + dispatch eKlaim.\n"
                "         Baca bagian 'Menguji di produksi' di README dulu.\n"
                "         Set ALLOW_PROD=1 kalau memang disengaja."
            )

    def warn_if_prod(self) -> None:
        """Peringatan yang tetap muncul walaupun ALLOW_PROD sudah 1."""
        asal = f" dari {PROFILE_SOURCE}" if PROFILE_SOURCE else ""
        print(f"[profil aktif: {ACTIVE_PROFILE or '(hanya .env)'}{asal}]  "
              f"{self.credential_fingerprint}")
        host = self.api_base_url.split("//")[-1].split("/")[0]
        is_prod_target = host.startswith("api.serenic.ai") or self.allow_prod
        if not is_prod_target:
            return
        print("!" * 78)
        print("TARGET PRODUKSI")
        if not self.is_direct:
            print("  Jalur ini MEMICU CASCADE: parsing -> analisis (LLM berbayar) -> dispatch eKlaim.")
            print("  Encounter sintetis akan muncul di data organisasi dan memakai kapasitas")
            print("  server eKlaim yang dipakai klaim asli.")
            print("  Pertimbangkan api-server_LOADTEST (lihat docker-compose.loadtest.yml)")
            print("  yang memakai IS_CODEX_API_V2_PROCESS=false sehingga cascade berhenti")
            print("  di worker parsing.")
        else:
            print("  Mode direct: menembak api-server_LOADTEST, cascade analisis dimatikan.")
            print("  Worker parsing tetap DIPAKAI BERSAMA dengan trafik asli.")
        print(f"  Organisasi: {self.org_label or self.consumer_id or '(dari apiKey)'}")
        print("!" * 78)
        print()

    def require_redis(self) -> None:
        missing = [n for n, v in (("REDIS_URL", self.redis_url), ("PARSING_QUEUE", self.parsing_queue)) if not v]
        if missing:
            sys.exit(
                f"[config] belum di-set: {', '.join(missing)}\n"
                "         Tanpa Redis, durasi job parsing tidak bisa diukur.\n"
                "         Jalankan `python tools/inspect_redis.py` untuk menemukan nama antrean."
            )

    def ensure_results_dir(self) -> Path:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        return self.results_dir


settings = Settings()
