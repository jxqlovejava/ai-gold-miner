"""SEC EDGAR 13F 持仓抓取 — 公开数据源, 无需 API key.

取代原先恒返回空的 whalewisdom 路径 (事故 2026-09-17: `_fetch_whalewisdom()`
返回 [], 导致 13F 信号长期使用占位常量)。

## SEC 的硬性要求

SEC 强制要求 User-Agent 声明**联系方式**, 否则一律 403
「Your Request Originates from an Undeclared Automated Tool」—— 实测
`ai-gold-miner/1.0 (research)` 被拒, 加上邮箱后 200。

联系方式经 `SEC_EDGAR_CONTACT` 环境变量或 `data/private/sec_edgar_contact.txt`
提供 (配置不入库)。缺失时不发请求, 由调用方回退并保持 `is_placeholder`。

## 13F 数据的三条实测约束 (2026-09-17)

1. **必须按 CUSIP 汇总** — 同一 CUSIP 会因不同 `otherManager` 子顾问出现多条
   `infoTable`。实测 Berkshire Q2 2026: 89 行 → 29 个唯一 CUSIP, 不汇总则持仓数虚高 3 倍。
2. **不能按 nameOfIssuer 匹配** — 该字段常被截断成通用名 (`ISHARES TR` /
   `SPDR SERIES TRUST`), 关键词匹配会大量误报。实测 `GOLDMAN SACHS ETF TR`、
   `THE MARYGOLD COMPANIES`、`GOLDEN SUN TECHNOLOGY`(中国科技公司)、
   `SILVERBOX CORP`(SPAC) 全部被 "GOLD"/"SILVER" 命中。**唯一可靠键是 CUSIP。**
3. **不能按 value 比较** — 该字段单位在不同 filer/时期不一致 (整元 vs 千元),
   实测同一标的隐含单价相差 1000 倍。**QoQ 比较只用 shares。**
"""

from __future__ import annotations

import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: 13F 申报窗口: 季末后 45 天
FILING_WINDOW_DAYS = 45

#: SEC 限流上限 10 req/s, 留一半余量
_MIN_REQUEST_INTERVAL = 0.2

_INFOTABLE_NS = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"

#: 已实测验证的黄金相关 CUSIP → 代号 (2026-09-17 从真实 Q2 2026 filing 反查)。
#: 每个条目均以「隐含单价 = value/shares」与真实标的价位交叉核对过。
GOLD_CUSIPS: dict[str, str] = {
    # --- 黄金/白银 ETF ---
    "78463V107": "GLD",     # SPDR GOLD TRUST        (5 份独立 filing 隐含单价一致 $368.38)
    "464285204": "IAU",     # ISHARES GOLD TRUST
    "46436F103": "IAUM",    # ISHARES GOLD TRUST MICRO
    "98149E303": "GLDM",    # SPDR GOLD MINI SHS ETF
    "92189F106": "GDX",     # VANECK GOLD MINERS ETF
    "92189F791": "GDXJ",    # VANECK JUNIOR GOLD MINERS ETF
    "38150K103": "AAAU",    # GOLDMAN SACHS PHYSICAL GOLD
    "921078101": "OUNZ",    # VANECK MERK GOLD ETF
    "46428Q109": "SLV",     # ISHARES SILVER TRUST
    "85208R101": "CEF",     # SPROTT PHYSICAL GOLD & SILVER TR
    # --- 大型金矿股 (nameOfIssuer 唯一, 无误报风险) ---
    "651639106": "NEWMONT",
    "06849F108": "BARRICK",
    "008474108": "AGNICO EAGLE",
    "G0378L100": "ANGLOGOLD",
    "496902404": "KINROSS",
    "38059T106": "GOLD FIELDS",
    "413216300": "HARMONY GOLD",
    "784730103": "SSR MINING",
    "697900108": "PAN AMERICAN SILVER",
    "422704106": "HECLA",
    "011532108": "ALAMOS GOLD",
    "29446Y502": "EQUINOX GOLD",
    "11777Q209": "B2GOLD",
    "32076V103": "FIRST MAJESTIC",
    "89679M104": "TRIPLE FLAG",
    "780287108": "ROYAL GOLD",
    "962879102": "WHEATON PRECIOUS",
    "152006102": "CENTERRA GOLD",
    "284902509": "ELDORADO GOLD",
    "450913108": "IAMGOLD",
    "675222400": "OCEANAGOLD",
    "811927102": "SEABRIDGE GOLD",
    "21077F100": "CONTANGO SILVER & GOLD",
    "927926303": "VISTA GOLD",
    "95805V108": "WESTERN COPPER & GOLD",
    "46655E100": "DAKOTA GOLD",
    "36352H100": "GALIANO GOLD",
    "44955L106": "I-80 GOLD",
    "64440N103": "NEW FOUND GOLD",
    "87283P109": "TRX GOLD",
    "01921D204": "ALLIED GOLD",
    "05223F106": "AUSTIN GOLD",
}


def resolve_contact() -> str | None:
    """解析 SEC 要求的联系标识.

    优先级: 环境变量 `SEC_EDGAR_CONTACT` → `data/private/sec_edgar_contact.txt`。

    Returns:
        形如 ``"ai-gold-miner someone@example.com"`` 的 User-Agent 值;
        未配置返回 None (调用方应跳过网络请求)。
    """
    def _normalise(raw: str) -> str | None:
        """SEC 要求 UA 含联系方式 —— 无邮箱一律视为未配置, 不发注定 403 的请求."""
        raw = (raw or "").strip()
        if "@" not in raw:
            return None
        # 裸邮箱补上应用名 (SEC 期望 "App Name contact@domain")
        return raw if " " in raw else f"ai-gold-miner {raw}"

    import os

    env = _normalise(os.environ.get("SEC_EDGAR_CONTACT") or "")
    if env:
        return env
    path = _PROJECT_ROOT / "data" / "private" / "sec_edgar_contact.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _normalise(text)


@dataclass(frozen=True)
class Holding:
    """单机构单标的持仓 (已按 CUSIP 汇总)."""

    cusip: str
    name: str
    shares: int


@dataclass(frozen=True)
class FilingRef:
    """一份 13F 申报的定位信息."""

    accession: str          # 含连字符, 如 0001193125-26-352200
    filing_date: str        # YYYY-MM-DD
    report_date: str        # YYYY-MM-DD, 报告期 (季末)
    form: str               # 13F-HR / 13F-HR/A


class EdgarClient:
    """SEC EDGAR 13F 客户端 (带限流与多层 HTTP 降级)."""

    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"

    def __init__(self, contact: str | None = None, timeout: float = 25.0) -> None:
        self.contact = contact if contact is not None else resolve_contact()
        self.timeout = timeout
        self._last_request = 0.0
        # 多机构并行取数时, 限流必须跨线程共享 —— 否则各线程各算各的,
        # 合计速率会超 SEC 的 10 req/s 上限
        self._throttle_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        """是否已配置 SEC 要求的联系标识."""
        return bool(self.contact)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        with self._throttle_lock:
            elapsed = time.time() - self._last_request
            if elapsed < _MIN_REQUEST_INTERVAL:
                time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
            self._last_request = time.time()

    def get(self, url: str) -> str | None:
        """取文本, 失败返回 None.

        注意: **必须绕过代理** —— SEC 走本机代理时 TLS 握手被拒
        (SSL_ERROR_SYSCALL), 直连正常。
        """
        if not self.configured:
            logger.debug("未配置 SEC_EDGAR_CONTACT, 跳过 EDGAR 请求")
            return None
        self._throttle()
        headers = {"User-Agent": self.contact or "", "Accept-Encoding": "gzip, deflate"}
        try:
            import httpx

            with httpx.Client(timeout=self.timeout, trust_env=False, follow_redirects=True) as c:
                r = c.get(url, headers=headers)
                if r.status_code == 200:
                    return r.text
                logger.debug(f"EDGAR httpx {r.status_code}: {url}")
        except Exception as e:
            logger.debug(f"EDGAR httpx 失败: {e}")

        # curl 兜底 (绕过 Python TLS 栈)
        try:
            res = subprocess.run(
                ["curl", "-sS", "--max-time", str(int(self.timeout)), "--noproxy", "*",
                 "--compressed", "-H", f"User-Agent: {self.contact}", url],
                capture_output=True, text=True, timeout=int(self.timeout) + 5,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout
            logger.debug(f"EDGAR curl 失败 (exit={res.returncode})")
        except Exception as e:
            logger.debug(f"EDGAR curl 异常: {e}")
        return None

    # ------------------------------------------------------------------
    # 申报列表
    # ------------------------------------------------------------------

    def list_13f_filings(self, cik: str, limit: int = 2) -> list[FilingRef]:
        """取某机构最近的 N 份 13F-HR (按申报日倒序).

        Args:
            cik: 10 位补零 CIK。
            limit: 取几份 (2 = 最新 + 上一期, 用于 QoQ 比较)。

        Returns:
            FilingRef 列表; 失败返回空列表。修正案 (13F-HR/A) 计入。
        """
        import json

        raw = self.get(self.SUBMISSIONS_URL.format(cik=cik))
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError as e:
            logger.debug(f"EDGAR submissions 解析失败 ({cik}): {e}")
            return []

        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        refs: list[FilingRef] = []
        for i, form in enumerate(forms):
            if not str(form).startswith("13F"):
                continue
            refs.append(
                FilingRef(
                    accession=recent["accessionNumber"][i],
                    filing_date=recent["filingDate"][i],
                    report_date=recent.get("reportDate", [""] * len(forms))[i] or "",
                    form=str(form),
                )
            )
            if len(refs) >= limit:
                break
        return refs

    # ------------------------------------------------------------------
    # 持仓表
    # ------------------------------------------------------------------

    def _infotable_names(self, cik: str, accession: str) -> list[str]:
        """列出一份申报里的候选 information table 文件名."""
        import json

        acc_nodash = accession.replace("-", "")
        raw = self.get(self.ARCHIVES_URL.format(cik=int(cik), accession=acc_nodash) + "index.json")
        if not raw:
            return []
        try:
            idx = json.loads(raw)
        except ValueError:
            return []
        items = (idx.get("directory") or {}).get("item") or []
        return [
            it["name"]
            for it in items
            if str(it.get("name", "")).endswith(".xml") and it.get("name") != "primary_doc.xml"
        ]

    def fetch_holdings(self, cik: str, accession: str) -> list[Holding]:
        """取一份 13F 的持仓, **已按 CUSIP 汇总** (见模块 docstring 约束 1).

        Args:
            cik: 10 位补零 CIK。
            accession: 含连字符的 accession number。

        Returns:
            Holding 列表 (按 shares 倒序); 失败返回空列表。
        """
        for name in self._infotable_names(cik, accession):
            url = self.ARCHIVES_URL.format(cik=int(cik), accession=accession.replace("-", "")) + name
            raw = self.get(url)
            if not raw:
                continue
            holdings = self.parse_infotable(raw)
            if holdings:
                return holdings
        return []

    @staticmethod
    def parse_infotable(xml_text: str) -> list[Holding]:
        """解析 13F information table XML, 按 CUSIP 聚合 shares.

        不读 ``value`` —— 其单位在不同 filer/时期不一致 (整元 vs 千元),
        比较持仓变化只能用 shares (见模块 docstring 约束 3)。

        Args:
            xml_text: information table 的 XML 原文。

        Returns:
            按 shares 倒序的 Holding 列表; XML 非法或为空返回空列表。
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.debug(f"13F infotable XML 解析失败: {e}")
            return []

        agg: dict[str, list[Any]] = {}
        for node in root.iter(f"{_INFOTABLE_NS}infoTable"):
            cusip = (node.findtext(f"{_INFOTABLE_NS}cusip") or "").strip().upper()
            if not cusip:
                continue
            shares_node = node.find(f"{_INFOTABLE_NS}shrsOrPrnAmt/{_INFOTABLE_NS}sshPrnamt")
            if shares_node is None:
                continue  # 缺持股数字的行按无效跳过, 不当作 0 股 (0 股语义是"清仓")
            try:
                shares = int((shares_node.text or "").strip())
            except ValueError:
                continue
            name = (node.findtext(f"{_INFOTABLE_NS}nameOfIssuer") or "").strip()
            entry = agg.setdefault(cusip, [0, name])
            entry[0] += shares
            if not entry[1]:
                entry[1] = name

        return sorted(
            (Holding(cusip=c, name=n, shares=s) for c, (s, n) in agg.items()),
            key=lambda h: h.shares,
            reverse=True,
        )

    def gold_positions(self, holdings: Iterable[Holding]) -> list[Holding]:
        """从持仓中筛出黄金相关标的 (按 CUSIP, 不按名称)."""
        return [h for h in holdings if h.cusip in GOLD_CUSIPS]
