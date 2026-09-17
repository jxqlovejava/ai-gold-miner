"""SEC EDGAR 13F 客户端测试 — 三条实测约束的回归锁定 (2026-09-17).

接入 EDGAR 取代恒返回空的 whalewisdom 路径时, 踩到三个会静默出错的数据陷阱,
每个都用真实 filing 验证过。本文件把它们固化成测试。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from gold_miner.data.edgar_13f import (
    GOLD_CUSIPS,
    EdgarClient,
    Holding,
    resolve_contact,
)
from gold_miner.data.institutional_13f import Institutional13FFetcher

_NS = 'xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable"'


def _table(*rows: tuple[str, str, int]) -> str:
    """构造 information table XML. rows: (cusip, name, shares)."""
    items = "".join(
        f"<infoTable><nameOfIssuer>{n}</nameOfIssuer><cusip>{c}</cusip>"
        f"<value>1000</value><shrsOrPrnAmt><sshPrnamt>{s}</sshPrnamt>"
        f"<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>"
        for c, n, s in rows
    )
    return f"<informationTable {_NS}>{items}</informationTable>"


class TestCusipAggregation:
    """约束 1: 同一 CUSIP 会因不同 otherManager 出现多条 infoTable."""

    def test_same_cusip_rows_are_summed(self) -> None:
        # 真实案例: Berkshire Q2 2026 有 89 行, 实际只有 29 个唯一 CUSIP
        holdings = EdgarClient.parse_infotable(
            _table(
                ("037833100", "APPLE INC", 100),
                ("037833100", "APPLE INC", 250),
                ("037833100", "APPLE INC", 50),
            )
        )
        assert len(holdings) == 1
        assert holdings[0].shares == 400

    def test_not_summed_would_inflate_position_count(self) -> None:
        holdings = EdgarClient.parse_infotable(
            _table(("78463V107", "SPDR GOLD TRUST", 10), ("78463V107", "SPDR GOLD TRUST", 20))
        )
        assert len(holdings) == 1, "不聚合会让持仓条数虚高"

    def test_sorted_by_shares_desc(self) -> None:
        holdings = EdgarClient.parse_infotable(
            _table(("A", "SMALL", 5), ("B", "BIG", 900))
        )
        assert [h.cusip for h in holdings] == ["B", "A"]

    def test_cusip_normalised_to_upper(self) -> None:
        holdings = EdgarClient.parse_infotable(_table(("78463v107", "SPDR GOLD TRUST", 1)))
        assert holdings[0].cusip == "78463V107"


class TestNoNameMatching:
    """约束 2: nameOfIssuer 常被截断成通用名, 按名称匹配会大量误报."""

    @pytest.mark.parametrize(
        ("cusip", "name"),
        [
            ("78462F103", "ST STR SPDR SP 500 ETF"),   # SPY — "SPDR" 但不是黄金
            ("381430453", "GOLDMAN SACHS ETF TR"),     # "GOLD" 是 GOLDMAN 的子串
            ("57403M104", "THE MARYGOLD COMPANIES INC"),  # "GOLD" 在 MARYGOLD 里
            ("G4013A115", "GOLDEN SUN TECHNOLOGY GROUP"),  # 中国科技公司
            ("G8148S107", "SILVERBOX CORP V"),         # SPAC
            ("464287200", "ISHARES CORE S&P 500"),     # 通用截断名
        ],
    )
    def test_non_gold_instruments_are_excluded(self, cusip: str, name: str) -> None:
        client = EdgarClient(contact="x@example.com")
        assert client.gold_positions([Holding(cusip, name, 1000)]) == []

    def test_real_gold_cusips_are_matched(self) -> None:
        client = EdgarClient(contact="x@example.com")
        holdings = [
            Holding("78463V107", "SPDR GOLD TRUST", 100),
            Holding("92189F106", "VANECK ETF TRUST", 200),  # 名称通用, 但 CUSIP 是 GDX
            Holding("464287200", "ISHARES CORE S&P 500", 999),
        ]
        matched = client.gold_positions(holdings)
        assert {h.cusip for h in matched} == {"78463V107", "92189F106"}

    def test_every_allowlist_entry_is_a_cusip_shaped_key(self) -> None:
        for cusip in GOLD_CUSIPS:
            assert len(cusip) == 9 and cusip.upper() == cusip, cusip


class TestParseRobustness:
    @pytest.mark.parametrize("bad", ["", "not xml", "<a><b>", "<?xml version='1.0'?>"])
    def test_malformed_returns_empty(self, bad: str) -> None:
        assert EdgarClient.parse_infotable(bad) == []

    def test_empty_table_returns_empty(self) -> None:
        assert EdgarClient.parse_infotable(f"<informationTable {_NS}></informationTable>") == []

    def test_row_without_cusip_skipped(self) -> None:
        xml = (
            f"<informationTable {_NS}><infoTable><nameOfIssuer>X</nameOfIssuer>"
            "<shrsOrPrnAmt><sshPrnamt>10</sshPrnamt></shrsOrPrnAmt></infoTable>"
            "</informationTable>"
        )
        assert EdgarClient.parse_infotable(xml) == []

    def test_non_numeric_shares_skipped(self) -> None:
        xml = (
            f"<informationTable {_NS}><infoTable><cusip>C1</cusip>"
            "<shrsOrPrnAmt><sshPrnamt>N/A</sshPrnamt></shrsOrPrnAmt></infoTable>"
            "</informationTable>"
        )
        assert EdgarClient.parse_infotable(xml) == []

    def test_missing_shares_node_skipped(self) -> None:
        xml = (
            f"<informationTable {_NS}><infoTable><cusip>C1</cusip>"
            "<shrsOrPrnAmt></shrsOrPrnAmt></infoTable></informationTable>"
        )
        assert EdgarClient.parse_infotable(xml) == []


class TestContactResolution:
    def test_env_var_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEC_EDGAR_CONTACT", "ai-gold-miner me@example.com")
        assert resolve_contact() == "ai-gold-miner me@example.com"

    def test_bare_email_gets_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEC_EDGAR_CONTACT", "me@example.com")
        assert resolve_contact() == "ai-gold-miner me@example.com"

    def test_no_email_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("SEC_EDGAR_CONTACT", "just-a-name")
        # 隔离真实配置文件: 否则会回落到 data/private/sec_edgar_contact.txt
        monkeypatch.setattr("gold_miner.data.edgar_13f._PROJECT_ROOT", tmp_path)
        assert resolve_contact() is None

    def test_missing_everywhere_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.delenv("SEC_EDGAR_CONTACT", raising=False)
        monkeypatch.setattr(
            "gold_miner.data.edgar_13f._PROJECT_ROOT", tmp_path
        )
        assert resolve_contact() is None


class TestUnconfiguredClient:
    """未配置联系标识时不得发请求 —— SEC 会 403, 白白拖慢 scan."""

    def test_configured_false(self) -> None:
        assert EdgarClient(contact="").configured is False

    def test_get_returns_none_without_request(self) -> None:
        client = EdgarClient(contact="")
        assert client.get("https://data.sec.gov/submissions/CIK0001067983.json") is None

    def test_list_filings_empty_when_unconfigured(self) -> None:
        assert EdgarClient(contact="").list_13f_filings("0001067983") == []


class _FakeEdgar(EdgarClient):
    """用固定响应替换 HTTP 层."""

    def __init__(self, submissions: dict, tables: dict[str, str]) -> None:
        super().__init__(contact="ai-gold-miner test@example.com")
        self._submissions = submissions
        self._tables = tables

    def get(self, url: str) -> str | None:
        if "submissions" in url:
            return json.dumps(self._submissions)
        if url.endswith("index.json"):
            return json.dumps({"directory": {"item": [{"name": "infotable.xml"}]}})
        for acc, xml in self._tables.items():
            if acc.replace("-", "") in url:
                return xml
        return None


def _submissions(*entries: tuple[str, str, str, str]) -> dict:
    return {
        "filings": {
            "recent": {
                "form": [e[0] for e in entries],
                "filingDate": [e[1] for e in entries],
                "reportDate": [e[2] for e in entries],
                "accessionNumber": [e[3] for e in entries],
                "primaryDocument": ["x.xml"] * len(entries),
            }
        }
    }


class TestListFilings:
    def test_returns_13f_only_in_order(self) -> None:
        edgar = _FakeEdgar(
            _submissions(
                ("10-K", "2026-03-01", "2025-12-31", "0001-26-000001"),
                ("13F-HR", "2026-08-14", "2026-06-30", "0001-26-000002"),
                ("13F-HR", "2026-05-15", "2026-03-31", "0001-26-000003"),
            ),
            {},
        )
        refs = edgar.list_13f_filings("0001067983", limit=2)
        assert [r.report_date for r in refs] == ["2026-06-30", "2026-03-31"]

    def test_limit_respected(self) -> None:
        edgar = _FakeEdgar(
            _submissions(
                *[(f"13F-HR", "2026-08-14", f"2026-0{i}-30", f"0001-26-00000{i}") for i in range(1, 5)]
            ),
            {},
        )
        assert len(edgar.list_13f_filings("0001067983", limit=1)) == 1

    def test_no_13f_returns_empty(self) -> None:
        edgar = _FakeEdgar(_submissions(("10-K", "2026-03-01", "2025-12-31", "a")), {})
        assert edgar.list_13f_filings("0001067983") == []

    def test_malformed_json_returns_empty(self) -> None:
        class Bad(EdgarClient):
            def get(self, url: str) -> str | None:
                return "{not json"

        assert Bad(contact="a@b.com").list_13f_filings("0001067983") == []


class TestDedupeAndQuarter:
    def test_dedupe_keeps_latest_per_report_date(self) -> None:
        from gold_miner.data.edgar_13f import FilingRef

        refs = [
            FilingRef("a", "2026-09-01", "2026-06-30", "13F-HR/A"),  # 修正案在前
            FilingRef("b", "2026-08-14", "2026-06-30", "13F-HR"),
            FilingRef("c", "2026-05-15", "2026-03-31", "13F-HR"),
        ]
        out = Institutional13FFetcher._dedupe_by_report_date(refs)
        assert [r.accession for r in out] == ["a", "c"]

    @pytest.mark.parametrize(
        ("date_str", "expected"),
        [
            ("2026-06-30", "Q2 2026"),
            ("2026-03-31", "Q1 2026"),
            ("2025-12-31", "Q4 2025"),
            ("2026-09-30", "Q3 2026"),
        ],
    )
    def test_quarter_from_date(self, date_str: str, expected: str) -> None:
        assert Institutional13FFetcher._quarter_from_date(date_str) == expected

    def test_quarter_from_bad_date_returns_input(self) -> None:
        assert Institutional13FFetcher._quarter_from_date("garbage") == "garbage"
        assert Institutional13FFetcher._quarter_from_date("") == "未知"


class TestEdgarSummaryFromRealFilings:
    """端到端 (HTTP 层被替换): 真实 filing 形状 → InstitutionalSummary."""

    CUR = _table(
        ("78463V107", "SPDR GOLD TRUST", 1000),
        ("651639106", "NEWMONT CORP", 500),
        ("037833100", "APPLE INC", 999999),   # 非黄金, 必须排除
    )
    PREV = _table(
        ("78463V107", "SPDR GOLD TRUST", 400),   # 增持
        ("651639106", "NEWMONT CORP", 900),      # 减持
        ("422704106", "HECLA MINING COMPANY", 300),  # 本期清仓
    )

    def _fetcher(self) -> Institutional13FFetcher:
        insts = {"Bridgewater Associates": {"cik": "0001350694"}}
        subs = _submissions(
            ("13F-HR", "2026-08-14", "2026-06-30", "ACC-cur"),
            ("13F-HR", "2026-05-15", "2026-03-31", "ACC-prev"),
        )
        edgar = _FakeEdgar(subs, {"ACC-cur": self.CUR, "ACC-prev": self.PREV})
        f = Institutional13FFetcher()
        f.TRACKED_INSTITUTIONS = insts  # type: ignore[assignment]
        return f, edgar

    def test_summary_built_from_real_holdings(self) -> None:
        f, edgar = self._fetcher()
        s = f._fetch_from_edgar(edgar)
        assert s is not None
        assert s.is_placeholder is False
        assert s.quarter == "Q2 2026"
        assert s.total_institutions == 1

    def test_non_gold_excluded_from_buyers(self) -> None:
        f, edgar = self._fetcher()
        s = f._fetch_from_edgar(edgar)
        assert s is not None
        assert all("APPLE" not in p.ticker for p in list(s.top_buyers) + list(s.top_sellers))

    def test_increase_goes_to_buyers_decrease_to_sellers(self) -> None:
        f, edgar = self._fetcher()
        s = f._fetch_from_edgar(edgar)
        assert s is not None
        buy_tickers = {p.ticker for p in s.top_buyers}
        sell_tickers = {p.ticker for p in s.top_sellers}
        assert "GLD" in buy_tickers          # 400 → 1000
        assert "NEWMONT" in sell_tickers     # 900 → 500
        assert "HECLA" in sell_tickers       # 清仓

    def test_closed_position_flagged(self) -> None:
        f, edgar = self._fetcher()
        s = f._fetch_from_edgar(edgar)
        assert s is not None
        hecla = next(p for p in s.top_sellers if p.ticker == "HECLA")
        assert hecla.is_closed is True
        assert hecla.shares == 0
        assert hecla.position_change_pct == -100.0

    def test_returns_none_when_no_institution_holds_gold(self) -> None:
        f, _ = self._fetcher()
        subs = _submissions(("13F-HR", "2026-08-14", "2026-06-30", "ACC-cur"))
        empty = _FakeEdgar(subs, {"ACC-cur": _table(("037833100", "APPLE INC", 1))})
        assert f._fetch_from_edgar(empty) is None, "无黄金持仓应回退, 而非输出 0/0"

    def test_value_usd_not_populated(self) -> None:
        """value 字段单位在不同 filer/时期不一致 (整元 vs 千元), 不可用于比较."""
        f, edgar = self._fetcher()
        s = f._fetch_from_edgar(edgar)
        assert s is not None
        assert all(p.value_usd == 0.0 for p in list(s.top_buyers) + list(s.top_sellers))
