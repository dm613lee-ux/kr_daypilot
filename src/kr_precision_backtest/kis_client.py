from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

from .env_config import KisCredentials


KST = ZoneInfo("Asia/Seoul")
BASE_URLS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "demo": "https://openapivts.koreainvestment.com:29443",
}
TIME_ITEM_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
TIME_DAILY_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
CURRENT_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
ASKING_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
CURRENT_CONCLUSION_PATH = "/uapi/domestic-stock/v1/quotations/inquire-ccnl"
TIME_ITEM_CONCLUSION_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemconclusion"
INDEX_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
TIME_INDEX_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice"
INDEX_CATEGORY_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-category-price"


class KisApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class KisClient:
    credentials: KisCredentials
    token_cache_path: Path

    @property
    def base_url(self) -> str:
        return BASE_URLS[self.credentials.env_dv]

    def fetch_intraday_minutes(
        self,
        ticker: str,
        *,
        market_div: str = "J",
        input_hour: str = "153000",
        include_past: bool = True,
    ) -> pd.DataFrame:
        ticker = str(ticker).zfill(6)
        params = {
            "FID_COND_MRKT_DIV_CODE": market_div,
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": input_hour,
            "FID_PW_DATA_INCU_YN": "Y" if include_past else "N",
            "FID_ETC_CLS_CODE": "",
        }
        body = self._get(TIME_ITEM_CHART_PATH, "FHKST03010200", params)
        output2 = body.get("output2") or []
        if not isinstance(output2, list):
            raise KisApiError("KIS minute response output2 is not a list.")
        return normalize_minute_bars(pd.DataFrame(output2), ticker=ticker)

    def fetch_historical_intraday_minutes(
        self,
        ticker: str,
        *,
        target_date: str,
        market_div: str = "J",
        input_hour: str = "153000",
        include_past: bool = True,
        include_fake_tick: bool = False,
    ) -> pd.DataFrame:
        ticker = str(ticker).zfill(6)
        date = "".join(ch for ch in str(target_date) if ch.isdigit())[:8]
        if len(date) != 8:
            raise KisApiError(f"Bad target_date for historical minute query: {target_date}")
        params = {
            "FID_COND_MRKT_DIV_CODE": market_div,
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": input_hour,
            "FID_INPUT_DATE_1": date,
            "FID_PW_DATA_INCU_YN": "Y" if include_past else "N",
            "FID_FAKE_TICK_INCU_YN": "Y" if include_fake_tick else "",
        }
        body = self._get(TIME_DAILY_CHART_PATH, "FHKST03010230", params)
        output2 = body.get("output2") or []
        if not isinstance(output2, list):
            raise KisApiError("KIS historical minute response output2 is not a list.")
        return normalize_minute_bars(pd.DataFrame(output2), ticker=ticker, default_date=date)

    def fetch_current_price(self, ticker: str, *, market_div: str = "J") -> dict[str, Any]:
        ticker = str(ticker).zfill(6)
        params = {
            "FID_COND_MRKT_DIV_CODE": market_div,
            "FID_INPUT_ISCD": ticker,
        }
        body = self._get(CURRENT_PRICE_PATH, "FHKST01010100", params)
        return _as_dict(body.get("output"))

    def fetch_orderbook_expected(self, ticker: str, *, market_div: str = "J") -> dict[str, Any]:
        ticker = str(ticker).zfill(6)
        params = {
            "FID_COND_MRKT_DIV_CODE": market_div,
            "FID_INPUT_ISCD": ticker,
        }
        body = self._get(ASKING_PRICE_PATH, "FHKST01010200", params)
        return {
            "output1": _as_dict(body.get("output1")),
            "output2": _as_dict(body.get("output2")),
        }

    def fetch_current_conclusion(self, ticker: str, *, market_div: str = "J") -> list[dict[str, Any]]:
        ticker = str(ticker).zfill(6)
        params = {
            "FID_COND_MRKT_DIV_CODE": market_div,
            "FID_INPUT_ISCD": ticker,
        }
        body = self._get(CURRENT_CONCLUSION_PATH, "FHKST01010300", params)
        return _as_list(body.get("output"))

    def fetch_time_item_conclusion(
        self,
        ticker: str,
        *,
        market_div: str = "J",
        input_hour: str = "093000",
    ) -> dict[str, Any]:
        ticker = str(ticker).zfill(6)
        params = {
            "FID_COND_MRKT_DIV_CODE": market_div,
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": _compact_time(input_hour),
        }
        body = self._get(TIME_ITEM_CONCLUSION_PATH, "FHPST01060000", params)
        return {
            "output1": _as_dict(body.get("output1")),
            "output2": _as_list(body.get("output2")),
        }

    def fetch_index_price(self, index_code: str) -> dict[str, Any]:
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": str(index_code),
        }
        body = self._get(INDEX_PRICE_PATH, "FHPUP02100000", params)
        return _as_dict(body.get("output"))

    def fetch_index_minutes(
        self,
        index_code: str,
        *,
        input_hour: str = "30",
        include_past: bool = True,
    ) -> dict[str, Any]:
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_ETC_CLS_CODE": "0",
            "FID_INPUT_ISCD": str(index_code),
            "FID_INPUT_HOUR_1": str(input_hour),
            "FID_PW_DATA_INCU_YN": "Y" if include_past else "N",
        }
        body = self._get(TIME_INDEX_CHART_PATH, "FHKUP03500200", params)
        return {
            "output1": _as_dict(body.get("output1")),
            "output2": _as_list(body.get("output2")),
        }

    def fetch_index_category_price(
        self,
        index_code: str,
        *,
        market_cls_code: str,
        belonging_cls_code: str = "0",
    ) -> dict[str, Any]:
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": str(index_code),
            "FID_COND_SCR_DIV_CODE": "20214",
            "FID_MRKT_CLS_CODE": market_cls_code,
            "FID_BLNG_CLS_CODE": belonging_cls_code,
        }
        body = self._get(INDEX_CATEGORY_PRICE_PATH, "FHPUP02140000", params)
        return {
            "output1": _as_dict(body.get("output1")),
            "output2": _as_list(body.get("output2")),
        }

    def _get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        token = self.access_token()
        query = urlencode(params)
        url = f"{self.base_url}{path}?{query}"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.credentials.app_key,
            "appsecret": self.credentials.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        return self._request_json("GET", url, headers=headers)

    def access_token(self) -> str:
        cached = self._read_cached_token()
        if cached:
            return cached
        return self._issue_token()

    def _issue_token(self) -> str:
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.credentials.app_key,
            "appsecret": self.credentials.app_secret,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8",
        }
        body = self._request_json("POST", url, headers=headers, payload=payload)
        token = str(body.get("access_token") or "")
        if not token:
            raise KisApiError("KIS token response did not include access_token.")
        expires_at = _parse_expiry(body)
        self._write_cached_token(token, expires_at)
        return token

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        raw = ""
        for attempt in range(3):
            request = Request(url, data=data, headers=headers, method=method)
            try:
                with urlopen(request, timeout=20) as response:
                    raw = response.read().decode("utf-8")
                break
            except HTTPError as exc:
                detail = _safe_error_detail(exc)
                if _is_rate_limit(detail) and attempt < 2:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                raise KisApiError(f"KIS HTTP error {exc.code}: {detail}") from exc
            except URLError as exc:
                raise KisApiError(f"KIS network error: {exc.reason}") from exc

        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KisApiError("KIS response was not valid JSON.") from exc

        rt_cd = str(body.get("rt_cd", "0"))
        if rt_cd not in {"0", ""}:
            msg = body.get("msg1") or body.get("msg_cd") or "unknown KIS API error"
            raise KisApiError(f"KIS API rejected request: {msg}")
        return body

    def _read_cached_token(self) -> str:
        if not self.token_cache_path.exists():
            return ""
        try:
            payload = json.loads(self.token_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        if payload.get("app_key_hash") != _hash_key(self.credentials.app_key):
            return ""
        expires_at_raw = str(payload.get("expires_at") or "")
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            return ""
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=KST)
        if expires_at <= datetime.now(tz=KST) + timedelta(minutes=5):
            return ""
        return str(payload.get("access_token") or "")

    def _write_cached_token(self, token: str, expires_at: datetime) -> None:
        self.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": token,
            "expires_at": expires_at.isoformat(),
            "app_key_hash": _hash_key(self.credentials.app_key),
        }
        self.token_cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_minute_bars(raw: pd.DataFrame, *, ticker: str, default_date: str | None = None) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "date",
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "trading_value",
                "acc_trading_value",
            ]
        )

    df = raw.copy()
    df["ticker"] = str(ticker).zfill(6)
    df["date"] = _first_existing(
        df,
        ["stck_bsop_date", "bsop_date", "date"],
        default=default_date or datetime.now(tz=KST).strftime("%Y%m%d"),
    )
    df["time"] = _first_existing(df, ["stck_cntg_hour", "bsop_hour", "time"], default="")
    df["open"] = _numeric_first(df, ["stck_oprc", "oprc", "open"])
    df["high"] = _numeric_first(df, ["stck_hgpr", "hgpr", "high"])
    df["low"] = _numeric_first(df, ["stck_lwpr", "lwpr", "low"])
    df["close"] = _numeric_first(df, ["stck_prpr", "prpr", "close"])
    df["volume"] = _numeric_first(df, ["cntg_vol", "vol", "volume"])
    df["trading_value"] = df["close"] * df["volume"]
    df["acc_trading_value"] = _numeric_first(df, ["acml_tr_pbmn", "tr_pbmn", "acc_trading_value"])

    columns = ["ticker", "date", "time", "open", "high", "low", "close", "volume", "trading_value", "acc_trading_value"]
    df = df[columns].copy()
    df["date"] = df["date"].astype(str).str.replace("-", "", regex=False).str[:8]
    df["time"] = df["time"].astype(str).str.replace(":", "", regex=False).str.zfill(6).str[:6]
    for column in ["open", "high", "low", "close", "volume", "trading_value"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    df = df[(df["time"] != "") & (df["close"] > 0)].drop_duplicates(["ticker", "date", "time"])
    return df.sort_values(["date", "time"]).reset_index(drop=True)


def _first_existing(df: pd.DataFrame, names: list[str], *, default: str) -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series([default] * len(df), index=df.index)


def _numeric_first(df: pd.DataFrame, names: list[str]) -> pd.Series:
    for name in names:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce").fillna(0.0)
    return pd.Series([0.0] * len(df), index=df.index)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _compact_time(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit()).zfill(6)[:6]


def _parse_expiry(body: dict[str, Any]) -> datetime:
    raw = body.get("access_token_token_expired") or body.get("expires_at")
    if raw:
        text = str(raw).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=KST)
            except ValueError:
                pass
    expires_in = int(float(body.get("expires_in") or 86400))
    return datetime.now(tz=KST) + timedelta(seconds=max(expires_in - 60, 60))


def _hash_key(app_key: str) -> str:
    return hashlib.sha256(app_key.encode("utf-8")).hexdigest()


def _safe_error_detail(exc: HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8")
        body = json.loads(raw)
        return str(body.get("msg1") or body.get("error_description") or body.get("msg_cd") or "request failed")
    except Exception:
        return "request failed"


def _is_rate_limit(detail: str) -> bool:
    text = str(detail)
    return "초당 거래건수" in text or "EGW00201" in text or "rate" in text.lower()
