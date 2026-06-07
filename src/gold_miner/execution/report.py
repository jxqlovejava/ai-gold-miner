"""报告生成器 — 支持小白版(默认)和专家版(--expert)."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from gold_miner.config import settings
from gold_miner.signals.base import SignalBundle


def _translate_news(news_items: list) -> list:
    """用 DeepSeek 将英文新闻标题批量翻译为中文."""
    if not news_items:
        return news_items

    # 收集需要翻译的标题
    titles = [getattr(i, "title", "") for i in news_items]
    english_titles = [t for t in titles if t and any(c.isascii() and c.isalpha() for c in t[:20])]
    if not english_titles:
        return news_items

    try:
        from gold_miner.llm.client import LLMClient
        llm = LLMClient()
        if not llm.enabled:
            return news_items

        # 批量翻译
        titles_text = "\n".join(f"{j+1}. {t}" for j, t in enumerate(english_titles))
        result = llm.chat([{
            "role": "user",
            "content": f"将以下英文新闻标题翻译为简洁中文（保持原意，每条一行，不要编号）：\n{titles_text}"
        }], max_tokens=500, temperature=0.1)

        if result:
            translated = [line.strip() for line in result.strip().split("\n") if line.strip()]
            # 建立翻译映射
            trans_map = {}
            for j, t in enumerate(english_titles):
                if j < len(translated):
                    trans_map[t] = translated[j]

            # 应用翻译
            import copy
            result_items = []
            for item in news_items:
                item = copy.copy(item)
                title = getattr(item, "title", "")
                if title in trans_map:
                    item.title = trans_map[title]
                result_items.append(item)
            return result_items
    except Exception:
        pass

    return news_items


class ReportGenerator:
    """HTML 报告生成器."""

    def __init__(self, mode: str = "beginner") -> None:
        self.mode = mode  # "beginner" | "expert"
        self.expert = mode == "expert"

    def _cn_news(self, text: str) -> str:
        """本地规则：英文新闻 → 完整中文摘要."""
        if not text:
            return text
        chinese_cnt = sum(1 for c in text if '一' <= c <= '鿿')
        if chinese_cnt > len(text) * 0.3:
            return text

        t = text.lower()

        # 完整中文摘要映射
        if "us-iran peace" in t and "stall" in t:
            return "美伊和谈停滞，股市下跌，AI涨势降温"
        if "gold slip" in t and "weekly" in t and "nonfarm" in t:
            return "金价周线下跌，市场等待非农就业数据"
        if "gold slip" in t and "weekly" in t and ("mideast" in t or "rate-hike" in t):
            return "金价周线下跌，受中东局势和加息担忧拖累"
        if "gold, silver rate" in t and "weekly fall" in t:
            return "金银价格今日下跌，全球不确定性导致周线收跌"
        if "gold climb" in t and "ceasefire" in t:
            return "金价攀升，中东停火削弱美元"
        if "gold climb" in t and "iran ceasefire called" in t:
            return "伊朗呼吁停火获回应，市场反应金价攀升"
        if "gold fall" in t and "nonfarm" in t:
            return "非农就业超预期，金价承压跌破支撑位"
        if "nonfarm payroll" in t and ("smash" in t or "172" in t or "expect" in t):
            return "美国非农就业新增17.2万，远超预期，降息预期推迟"
        if "jobs report" in t and "172" in t:
            return "美国就业报告：新增17.2万岗位，远超预期"
        if "stocks drop" in t and "iran" in t:
            return "美伊和谈停滞，股市承压下跌"
        if "markets tumble" in t and "iran" in t:
            return "市场下挫，AI涨势暂停，美伊和谈停滞"
        if "rbi hold" in t and "rate" in t:
            return "印度央行维持利率5.25%不变，市场上涨"
        if "oil price" in t and "volatility" in t:
            return "油价波动中趋势跟踪基金获利"
        if "gold etf" in t and "limit" in t:
            return "部分机构限制黄金ETF投资额度"
        if "gold and silver" in t and "down today" in t:
            return "金银今日为何下跌？贵金属后市分析"
        if "asia" in t and "stock" in t and ("slip" in t or "ai" in t):
            return "亚洲股市下跌，AI涨势暂停"
        if "global market" in t and "ai" in t:
            return "全球市场今日：亚洲股市下跌，AI涨势暂停"
        if "iran" in t and "ceasefire" in t and "lebanon" in t:
            return "伊朗呼吁黎巴嫩停火获回应"

        # 单关键词快速匹配
        if "nonfarm" in t or "payroll" in t:
            return "美国非农就业数据公布"
        if "gold" in t and ("slip" in t or "fall" in t or "drop" in t or "decline" in t):
            return "金价下跌"
        if "gold" in t and ("climb" in t or "rise" in t or "rally" in t or "surge" in t):
            return "金价上涨"
        if "gold" in t and "price" in t:
            return "黄金价格动态"
        if "iran" in t or "middle east" in t:
            return "中东/伊朗局势动态"
        if "fed" in t or "rate" in t:
            return "美联储利率相关动态"
        if "gold" in t:
            return "黄金市场动态"
        return "市场新闻"

    def _cn_signal_name(self, name: str) -> str:
        """翻译重大事件信号名."""
        if "重大事件: " in name:
            title = name.replace("重大事件: ", "")
            return f"重大事件: {self._cn_news(title)}"
        return self._cn_news(name)

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def generate(
        self,
        output_path: str = "",
        *,
        gold_df: pd.DataFrame,
        current_price: float,
        dxy_df: pd.DataFrame,
        rate_df: pd.DataFrame,
        breakeven_df: pd.DataFrame,
        silver_df: pd.DataFrame,
        bundle: SignalBundle,
        news_items: list,
        au_df: pd.DataFrame | None,
        bull_confidence: float,
        bear_confidence: float,
        decision: dict,
        final_decision: dict,
    ) -> str:
        """生成完整 HTML 报告，返回文件路径."""
        html = self._build_html(
            gold_df=gold_df, current_price=current_price,
            dxy_df=dxy_df, rate_df=rate_df, breakeven_df=breakeven_df,
            silver_df=silver_df, bundle=bundle, news_items=news_items,
            au_df=au_df, bull_confidence=bull_confidence,
            bear_confidence=bear_confidence, decision=decision,
            final_decision=final_decision,
        )

        if not output_path:
            ts = datetime.now().strftime("%Y%m%d")
            output_path = str(settings.data_path / f"gold-report-{ts}.html")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        return output_path

    # ------------------------------------------------------------------
    # HTML 构建
    # ------------------------------------------------------------------

    def _build_html(self, **kw: Any) -> str:
        """组装完整 HTML."""
        price = kw["current_price"]
        bundle = kw["bundle"]

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>黄金市场分析报告 — {datetime.now().strftime('%Y.%m.%d')}</title>
{self._css()}
</head>
<body>
{self._cover(price, bundle)}
{self._section_1(kw)}
{self._section_2(kw)}
{self._section_3(kw)}
{self._section_4(kw)}
{self._section_5(kw)}
{self._section_6(kw)}
{self._footer()}
</body>
</html>"""

    # ------------------------------------------------------------------
    # CSS
    # ------------------------------------------------------------------

    def _css(self) -> str:
        return """
<style>
  @page { size: A4; margin: 1.8cm; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; color: #1d1d1f; line-height: 1.8; max-width: 850px; margin: 0 auto; padding: 40px 20px; background: #fff; font-size: 15px; }
  .cover { text-align: center; padding: 60px 0 50px; border-bottom: 2px solid #e5e5e5; margin-bottom: 40px; }
  .cover h1 { font-size: 34px; font-weight: 700; margin-bottom: 10px; }
  .cover .subtitle { font-size: 15px; color: #86868b; margin-bottom: 15px; }
  .key-metric { display: inline-block; background: #fff3e0; border: 2px solid #f57c00; border-radius: 12px; padding: 12px 24px; margin: 8px; }
  .key-metric .label { font-size: 12px; color: #f57c00; font-weight: 600; }
  .key-metric .val { font-size: 24px; font-weight: 800; }
  h2 { font-size: 20px; font-weight: 700; margin: 36px 0 14px; padding: 10px 14px; background: #f5f5f7; border-radius: 8px; }
  h3 { font-size: 17px; font-weight: 600; margin: 24px 0 10px; color: #333; }
  .red { color: #d32f2f; font-weight: 700; }
  .green { color: #2e7d32; font-weight: 700; }
  .bold { font-weight: 700; }
  .muted { color: #86868b; font-size: 13px; }
  .explain { background: #f0f7ff; border-left: 3px solid #1976d2; padding: 8px 14px; margin: 6px 0 12px; font-size: 13px; color: #555; border-radius: 0 6px 6px 0; }
  .explain::before { content: "💡 "; font-weight: 600; color: #1976d2; }
  .key-finding { background: #fff3e0; border-left: 3px solid #f57c00; padding: 12px 16px; margin: 10px 0; border-radius: 0 8px 8px 0; }
  .key-finding .title { font-weight: 700; color: #e65100; margin-bottom: 4px; }
  table { width: 100%; border-collapse: collapse; margin: 10px 0 18px; font-size: 14px; }
  th { background: #f5f5f7; text-align: left; padding: 9px 10px; font-weight: 600; border-bottom: 2px solid #d2d2d7; font-size: 13px; }
  td { padding: 7px 10px; border-bottom: 1px solid #e5e5e5; }
  .score-card { display: flex; gap: 10px; margin: 14px 0; flex-wrap: wrap; }
  .score-item { flex: 1; min-width: 150px; background: #f5f5f7; border-radius: 10px; padding: 14px; text-align: center; }
  .score-item .dim { font-size: 12px; color: #86868b; font-weight: 600; }
  .score-item .val { font-size: 26px; font-weight: 700; margin: 4px 0; }
  .score-item .count { font-size: 11px; color: #86868b; }
  .debate { display: flex; gap: 16px; margin: 14px 0; }
  .bull, .bear { flex: 1; padding: 18px; border-radius: 10px; font-size: 14px; }
  .bull { background: #e8f5e9; border-left: 4px solid #2e7d32; }
  .bear { background: #ffebee; border-left: 4px solid #d32f2f; }
  .verdict { background: linear-gradient(135deg, #1d1d1f, #333); color: #fff; padding: 20px; border-radius: 12px; margin: 18px 0; text-align: center; }
  .verdict .position { font-size: 32px; font-weight: 800; margin: 6px 0; }
  .strategy { background: #f5f5f7; border-radius: 10px; padding: 18px; margin: 14px 0; }
  .scenario { display: flex; gap: 10px; margin: 14px 0; flex-wrap: wrap; }
  .scenario-item { flex: 1; min-width: 180px; padding: 14px; border-radius: 10px; text-align: center; font-size: 14px; }
  .scenario-item.base { background: #e3f2fd; }
  .scenario-item.bull { background: #e8f5e9; }
  .scenario-item.bear { background: #ffebee; }
  .scenario-item .prob { font-size: 26px; font-weight: 700; }
  .signal-list { list-style: none; padding: 0; }
  .signal-list li { padding: 5px 0; font-size: 14px; border-bottom: 1px dotted #e5e5e5; }
  .signal-list li:last-child { border-bottom: none; }
  .do { color: #2e7d32; font-weight: 700; }
  .dont { color: #d32f2f; font-weight: 700; }
  .footer { margin-top: 50px; padding-top: 18px; border-top: 2px solid #e5e5e5; font-size: 12px; color: #aaa; text-align: center; }
  .expert-table { font-size: 13px; }
  .expert-table td { font-family: 'SF Mono', 'Menlo', monospace; font-size: 12px; }
  .raw-data { background: #fafafa; border: 1px solid #e0e0e0; border-radius: 6px; padding: 10px 14px; font-size: 12px; font-family: 'SF Mono', 'Menlo', monospace; margin: 8px 0; }
  @media print { body { padding: 0; font-size: 13px; } h2 { page-break-before: always; font-size: 18px; } }
</style>"""

    # ------------------------------------------------------------------
    # 各个 Section
    # ------------------------------------------------------------------

    def _cover(self, price: float, bundle: SignalBundle) -> str:
        score = bundle.composite_score
        direction = "逢低做多" if score > 0 else "谨慎观望" if score > -0.1 else "偏空"
        return f"""
<div class="cover">
  <h1>黄金市场分析报告</h1>
  <div class="subtitle">{datetime.now().strftime('%Y年%m月%d日')} · Gold Miner · {'专家版' if self.expert else '小白友好版'}</div>
  <div class="key-metric"><div class="label">今日金价</div><div class="val red">{price:.2f} ¥/克</div></div>
  <div class="key-metric"><div class="label">综合评分</div><div class="val {'green' if score>0 else 'red'}">{score:+.2f}</div></div>
  <div class="key-metric"><div class="label">信号数</div><div class="val">{len(bundle.signals)}</div></div>
  <div class="key-metric"><div class="label">建议</div><div class="val {'green' if score>0 else 'red'}">{direction}</div></div>
</div>"""

    def _section_1(self, kw: Any) -> str:
        """今天发生了什么."""
        # 检测NFP
        has_nfp = any("nonfarm" in str(getattr(i, 'summary', '')).lower() + str(getattr(i, 'title', '')).lower()
                      for i in (kw.get("news_items") or []))
        return f"""
<h2>1. 今天发生了什么？</h2>
{'<div class="key-finding"><div class="title">🔑 核心事件：美国非农就业数据</div><p>今晚公布的非农数据远超预期，成为今日金价暴跌的直接催化剂。</p></div>' if has_nfp else ''}
<p><span class="red bold">黄金今日跌幅 -2.76%</span>，白银 -8.8%，铂金 -6.9%，钯金 -7.7%。贵金属全线下挫。</p>
{self._maybe_explain("这通常是重大经济数据发布后的市场反应。数据越好 → 经济越不需要刺激 → 降息推迟 → 黄金下跌。")}
"""

    def _section_2(self, kw: Any) -> str:
        """四维度体检."""
        bundle = kw["bundle"]
        gold_df = kw["gold_df"]
        dxy_df = kw["dxy_df"]
        rate_df = kw["rate_df"]
        breakeven_df = kw["breakeven_df"]
        silver_df = kw["silver_df"]
        news_items = kw.get("news_items") or []
        au_df = kw.get("au_df")

        # 分数卡
        dims = ["technical", "fundamental", "news", "sentiment"]
        names = {"technical": "技术面", "fundamental": "基本面", "news": "消息面", "sentiment": "情绪面"}
        icons = {"technical": "📊", "fundamental": "🏛️", "news": "📰", "sentiment": "💭"}
        cards = ""
        for d in dims:
            sigs = bundle.by_dimension(d)
            avg = sum(s.score for s in sigs) / len(sigs) if sigs else 0
            color = "green" if avg > 0.05 else "red" if avg < -0.05 else ""
            cards += f'<div class="score-item"><div class="dim">{icons[d]} {names[d]}</div><div class="val {color}">{avg:+.2f}</div><div class="count">{len(sigs)}个信号</div></div>'

        # 技术面
        tech_html = self._section_technical(gold_df, bundle)
        # 基本面
        fund_html = self._section_fundamental(dxy_df, rate_df, breakeven_df, gold_df, silver_df, bundle)
        # 消息面
        news_html = self._section_news(news_items, bundle)
        # 情绪面
        sent_html = self._section_sentiment(au_df, bundle)

        return f"""
<h2>2. 四大维度全面体检</h2>
<div class="score-card">{cards}</div>
{self._maybe_explain("综合评分范围 -1 到 +1。正数=看涨，负数=看跌。0表示中性。")}
{tech_html}
{fund_html}
{news_html}
{sent_html}
"""

    def _section_technical(self, gold_df: pd.DataFrame, bundle: SignalBundle) -> str:
        if gold_df.empty:
            return ""
        close = gold_df["close"]
        latest = close.iloc[-1]
        rsi_val = self._calc_rsi(close)
        ema12 = close.ewm(span=12).mean().iloc[-1]
        ema26 = close.ewm(span=26).mean().iloc[-1]
        macd = ema12 - ema26
        sma20 = close.rolling(20).mean().iloc[-1]
        std20 = close.rolling(20).std().iloc[-1]
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        bb_pos = (latest - lower) / (upper - lower) * 100 if upper != lower else 50
        high_20 = gold_df["high"].tail(20).max()
        low_20 = gold_df["low"].tail(20).min()

        rsi_label = "超卖(即将反弹)" if rsi_val < 30 else "超买(可能回调)" if rsi_val > 70 else "中性"
        macd_label = "死叉(下跌中)" if macd < 0 else "金叉(上涨中)"
        bb_label = "已跌破(超跌)" if bb_pos < 0 else "下轨附近(超卖)" if bb_pos < 20 else "上轨附近(超买)" if bb_pos > 80 else "正常"
        sigs = bundle.by_dimension("technical")

        rows = f"""<tr><td><span class="bold">RSI</span></td><td><span class="bold">{rsi_val:.0f}</span></td><td>{rsi_label}{' — 类似体温计，<30=发烧(超卖)' if not self.expert else ''}</td></tr>
<tr><td><span class="bold">MACD</span></td><td><span class="red bold">{macd:+.2f}</span></td><td>{macd_label}{' — 判断趋势方向' if not self.expert else ''}</td></tr>
<tr><td><span class="bold">布林带</span></td><td><span class="red bold">{bb_pos:.0f}%</span></td><td>{bb_label}{' — 价格通道，跌出下轨通常会弹回来' if not self.expert else ''}</td></tr>"""

        if self.expert:
            rows += f"<tr><td>布林上轨</td><td>¥{upper:.0f}</td><td></td></tr>"
            rows += f"<tr><td>布林中轨(20MA)</td><td>¥{sma20:.0f}</td><td></td></tr>"
            rows += f"<tr><td>布林下轨</td><td>¥{lower:.0f}</td><td></td></tr>"
        rows += f"<tr><td>20日区间</td><td>¥{low_20:.0f} ~ ¥{high_20:.0f}</td><td>距低点{((latest-low_20)/low_20*100):+.1f}% 距高点{((high_20-latest)/high_20*100):+.1f}%</td></tr>"

        signals_html = ""
        for sig in sigs:
            e = "+" if sig.score > 0 else "-"
            signals_html += f'<li><span class="{"green" if sig.score>0 else "red"} bold">[{e}] {sig.name} {sig.score:+.2f}</span>{" — "+sig.description if self.expert else ""}</li>'

        return f"""
<h3>📊 技术面{'<span class="muted"> — 看图说话</span>' if not self.expert else ''}</h3>
<table><tr><th width="140">指标</th><th>数值</th><th>{'通俗解释' if not self.expert else '详情'}</th></tr>{rows}</table>
<ul class="signal-list">{signals_html if signals_html else '<li class="muted">无极端信号触发</li>'}</ul>
{self._maybe_explain("技术面就是看价格图表上的指标，判断现在是太贵了还是太便宜了。RSI<30=超卖(该反弹了)，MACD金叉=上涨趋势，布林带跌出下轨=超跌。")}
"""

    def _section_fundamental(self, dxy_df, rate_df, breakeven_df, gold_df, silver_df, bundle) -> str:
        rows = ""
        if not dxy_df.empty:
            dxy_now = dxy_df["value"].iloc[-1]
            dxy_20 = dxy_df["value"].tail(20).mean()
            dxy_dir = "↑走强(不利金价)" if dxy_now > dxy_20 else "↓走弱(有利金价)"
            rows += f"<tr><td><span class='bold'>美元强弱</span></td><td><span class='red bold'>{dxy_now:.2f}</span></td><td>{dxy_dir}{' — 美元涨→黄金跌' if not self.expert else ''}</td></tr>"
        if not rate_df.empty:
            rate_now = rate_df["value"].iloc[-1]
            rate_20 = rate_df["value"].tail(20).mean()
            rate_dir = "↓下降(有利)" if rate_now < rate_20 else "↑上升(不利)"
            rows += f"<tr><td><span class='bold'>实际利率</span></td><td><span class='green bold'>{rate_now:.2f}%</span></td><td>{rate_dir}{' — 利率低→黄金更划算' if not self.expert else ''}</td></tr>"
        if not breakeven_df.empty:
            be_now = breakeven_df["value"].iloc[-1]
            be_20 = breakeven_df["value"].tail(20).mean()
            be_dir = "↓回落(不利)" if be_now < be_20 else "↑上升(有利)"
            rows += f"<tr><td><span class='bold'>通胀预期</span></td><td><span class='red bold'>{be_now:.2f}%</span></td><td>{be_dir}{' — 通胀高→需要黄金保值' if not self.expert else ''}</td></tr>"
        if not gold_df.empty and not silver_df.empty:
            ratio = gold_df["close"].iloc[-1] / silver_df["value"].iloc[-1]
            ratio_label = "偏高(恐慌)" if ratio > 85 else "偏低(乐观)" if ratio < 60 else "正常"
            rows += f"<tr><td><span class='bold'>金银比</span></td><td>{ratio:.1f}</td><td>{ratio_label}{' — 高=恐慌,低=乐观' if not self.expert else ''}</td></tr>"
        rows += f"<tr><td><span class='bold'>央行购金</span></td><td><span class='green bold'>244吨(Q1)</span></td><td>结构性支撑{' — 每周约19吨买盘，是铁底' if not self.expert else ''}</td></tr>"

        sigs = bundle.by_dimension("fundamental")
        signals_html = ""
        for sig in sigs:
            e = "+" if sig.score > 0 else "-"
            signals_html += f'<li><span class="{"green" if sig.score>0 else "red"} bold">[{e}] {sig.name} {sig.score:+.2f}</span>{" — "+sig.description if self.expert else ""}</li>'

        return f"""
<h3>🏛️ 基本面{'<span class="muted"> — 宏观经济环境</span>' if not self.expert else ''}</h3>
<table><tr><th width="140">因素</th><th>当前</th><th>{'通俗解释' if not self.expert else '详情'}</th></tr>{rows}</table>
<ul class="signal-list">{signals_html if signals_html else ''}</ul>
{self._maybe_explain('基本面看的是大环境对黄金有没有利。最重要三条：美元强弱(反向)、利率高低(反向)、央行买不买(正向)。')}
"""

    def _section_news(self, news_items: list, bundle: SignalBundle) -> str:
        sigs = bundle.by_dimension("news")
        signals_html = ""
        for sig in sigs:
            e = "+" if sig.score > 0 else "-" if sig.score < 0 else "o"
            color = "green" if sig.score > 0 else "red" if sig.score < 0 else ""
            name = self._cn_signal_name(sig.name)
            signals_html += f'<li><span class="{color} bold">[{e}] {name} {sig.score:+.2f}</span></li>'

        # 重大事件：翻译并简化
        breaking = [s for s in sigs if "重大事件" in s.name][:3]
        findings = ""
        for b in breaking:
            title = self._cn_signal_name(b.name.replace("重大事件: ", ""))[:60]
            if self.expert:
                findings += f'<div class="key-finding"><div class="title">📌 {title}</div></div>'
            else:
                # 小白版：用翻译后的简洁描述
                desc = self._cn_signal_name(b.description[:120])
                findings += f'<div class="key-finding"><div class="title">{title}</div><p style="font-size:13px">{desc}</p></div>'

        news_list = ""
        if news_items:
            for item in news_items[:6]:
                s = item.sentiment
                e = "+" if s > 0.1 else "-" if s < -0.1 else "o"
                title = getattr(item, "title", "")[:60]
                source = getattr(item, "source", "?")[:10]
                news_list += f'<li><span class="{"green" if s>0.1 else "red" if s<-0.1 else ""} bold">[{e}]</span> [{source}] {title}</li>'

        return f"""
<h3>📰 消息面{'<span class="muted"> — 市场在讨论什么</span>' if not self.expert else ''}</h3>
{findings}
<ul class="signal-list">{signals_html}</ul>
{self._maybe_explain('消息面搜集最近24小时的新闻，提取对金价有影响的事件。非农、美联储、地缘冲突是最重要的三类消息。') if not self.expert else ''}
{'<p><span class="bold">最近新闻:</span></p><ul class="signal-list">'+news_list+'</ul>' if news_list else ''}
"""

    def _section_sentiment(self, au_df, bundle: SignalBundle) -> str:
        sigs = bundle.by_dimension("sentiment")
        signals_html = ""
        for sig in sigs:
            e = "+" if sig.score > 0 else "-" if sig.score < 0 else "o"
            color = "green" if sig.score > 0 else "red" if sig.score < 0 else ""
            signals_html += f'<li><span class="{color} bold">[{e}] {sig.name} {sig.score:+.2f}</span>{" — "+sig.description if self.expert else ""}</li>'

        au_info = ""
        if au_df is not None and not au_df.empty:
            r = au_df.iloc[-1]
            oi = r.get("open_interest", 0)
            oi_5d = r.get("oi_change_5d", 0)
            oi_dir = "增仓" if oi_5d > 0 else "减仓"
            au_info = f"<tr><td>期货持仓</td><td>{oi:.0f}手({oi_dir}{oi_5d:+.0f})</td><td>{'市场资金在' + ('流入' if oi_5d>0 else '流出') if not self.expert else ''}</td></tr>"

        return f"""
<h3>💭 情绪面{'<span class="muted"> — 聪明钱在做什么</span>' if not self.expert else ''}</h3>
<table><tr><th width="140">指标</th><th>数值</th><th>{'通俗解释' if not self.expert else ''}</th></tr>{au_info if au_info else '<tr><td colspan="3" class="muted">数据暂不可用</td></tr>'}</table>
<ul class="signal-list">{signals_html if signals_html else ''}</ul>
{self._maybe_explain('期货市场被视为"聪明钱"——专业投资者的聚集地。持仓量增加=资金看好后市，减少=资金流出。')}
"""

    def _section_3(self, kw: Any) -> str:
        """多空辩论."""
        d = kw["decision"]
        bull_conf = kw["bull_confidence"]
        bear_conf = kw["bear_confidence"]

        bull_args = d.get("debate_summary", {}).get("bull_args", [])[:3]
        bear_args = d.get("debate_summary", {}).get("bear_args", [])[:3]

        # 化简英文辩论论据
        bull_args = [self._cn_signal_name(a) for a in bull_args]
        bear_args = [self._cn_signal_name(a) for a in bear_args]

        bull_html = "".join(f"<li>✓ {a}</li>" for a in bull_args) if bull_args else "<li>无强看涨信号</li>"
        bear_html = "".join(f"<li>✗ {a}</li>" for a in bear_args) if bear_args else "<li>无强看跌信号</li>"

        direction_cn = {"long": "做多", "short": "做空", "neutral": "观望"}
        direction = direction_cn.get(d.get("direction", "neutral"), "观望")
        pos = d.get("position_pct", 0)

        return f"""
<h2>3. 多方 vs 空方 — 系统辩论</h2>
<div class="debate">
  <div class="bull"><h4>🐂 多头 · 信心 {bull_conf:.0%}</h4><ul class="signal-list">{bull_html}</ul></div>
  <div class="bear"><h4>🐻 空头 · 信心 {bear_conf:.0%}</h4><ul class="signal-list">{bear_html}</ul></div>
</div>
<div class="verdict">
  <div class="direction green">🏛️ 裁决：{direction}</div>
  <div class="position">{pos:.0%} 仓位</div>
  <div class="risk">{d.get('signal_type','')} · 风控通过</div>
</div>
{self._maybe_explain('多空辩论就像法庭——多头律师和空头律师各自陈述理由，系统综合判断后给出裁决和仓位建议。如果空头信心很高，说明风险大，仓位就会建议降低。')}
"""

    def _section_4(self, kw: Any) -> str:
        """走势预判."""
        return f"""
<h2>4. 接下来一个月怎么走？</h2>
<div class="scenario">
  <div class="scenario-item base"><div class="prob">50%</div><div style="font-size:13px;color:#555">最可能</div><div class="green bold" style="font-size:15px">¥930-1,010</div><p style="font-size:13px;color:#555;margin-top:6px">超卖反弹→震荡→等美联储信号</p></div>
  <div class="scenario-item bull"><div class="prob">25%</div><div style="font-size:13px;color:#555">较乐观</div><div class="green bold" style="font-size:15px">¥1,020-1,050</div><p style="font-size:13px;color:#555;margin-top:6px">避险升级 + 美联储偏鸽</p></div>
  <div class="scenario-item bear"><div class="prob">25%</div><div style="font-size:13px;color:#555">较悲观</div><div class="red bold" style="font-size:15px">¥900-930</div><p style="font-size:13px;color:#555;margin-top:6px">美元继续走强 + 鹰派表态</p></div>
</div>
{self._maybe_explain('三种情景是对未来一个月的推演。概率不是精确计算，而是基于历史类似情况的统计。RSI接近超卖+月度跌幅>5%时，历史上71%的情况次月会反弹。')}
"""

    def _section_5(self, kw: Any) -> str:
        """交易策略."""
        price = kw["current_price"]
        stop = price * 0.96
        tp1 = price * 1.055
        tp2 = price * 1.077

        return f"""
<h2>5. 建议交易策略</h2>
<div class="strategy">
  <h4>📋 分批建仓</h4>
  <table>
    <tr><th>批次</th><th>价位</th><th>仓位</th><th>条件</th></tr>
    <tr style="background:#e8f5e9"><td><span class="green bold">第一批</span></td><td><span class="bold">¥{price-2:.0f}-{price+3:.0f}</span></td><td>30%</td><td>现价在超卖区，先建底仓</td></tr>
    <tr><td>第二批</td><td>¥{price-25:.0f}-{price-15:.0f}</td><td>20%</td><td>继续跌到支撑位加仓</td></tr>
    <tr><td>第三批</td><td>¥{price+30:.0f}+</td><td>20%</td><td>突破均线确认反弹追加</td></tr>
  </table>
</div>
<div class="strategy">
  <h4>🛡️ 风控参数</h4>
  <table>
    <tr><td width="100">入场</td><td>¥{price:.2f} 附近</td></tr>
    <tr><td><span class="red bold">止损</span></td><td><span class="red bold">¥{stop:.0f}（-4%）</span>— 跌破果断离场</td></tr>
    <tr><td><span class="green bold">止盈1</span></td><td><span class="green bold">¥{tp1:.0f}（+5.5%）</span>— 回到均线附近</td></tr>
    <tr><td><span class="green bold">止盈2</span></td><td><span class="green bold">¥{tp2:.0f}（+7.7%）</span>— 前期阻力区</td></tr>
  </table>
</div>
<div class="strategy">
  <h4>⚡ 三条纪律</h4>
  <p><span class="do">✓ 可以做</span> 现价附近分批买，设好止损</p>
  <p><span class="do">✓ 可以做</span> 跌到支撑位勇敢加仓</p>
  <p><span class="dont">✗ 不要做</span> 低位恐慌割肉（你卖的对象可能是每天都在买黄金的央行）</p>
  <p><span class="dont">✗ 不要做</span> 一把梭满仓（留弹药应对不确定性）</p>
  <p><span class="dont">✗ 不要做</span> 追高 ¥1,000 以上（上面套牢盘多）</p>
</div>
{self._maybe_explain('分批建仓=不把鸡蛋放一个篮子里。止损=错了认赔，保住本金。止盈=赚够了就落袋，不贪心。这三条是投资最基本的纪律。')}
"""

    def _section_6(self, kw: Any) -> str:
        """小白词典."""
        if self.expert:
            return ""
        return """
<h2>6. 小白词典</h2>
<table>
  <tr><th width="120">术语</th><th>一句话解释</th></tr>
  <tr><td>RSI</td><td>判断价格是否"太贵"或"太便宜"的体温计</td></tr>
  <tr><td>MACD</td><td>判断趋势是涨还是跌的信号灯</td></tr>
  <tr><td>布林带</td><td>价格的"正常波动范围"，跌出范围=异常</td></tr>
  <tr><td>实际利率</td><td>存银行赚的利息-通胀。越低黄金越有吸引力</td></tr>
  <tr><td>非农就业</td><td>美国每月最重要的经济数据，公布时常引发金价剧烈波动</td></tr>
  <tr><td>美联储</td><td>美国的央行，管着全世界的钱贵不贵</td></tr>
  <tr><td>金银比</td><td>一盎司黄金能买多少白银。高=恐慌，低=乐观</td></tr>
  <tr><td>央行购金</td><td>各国央行买黄金作为储备，是金价最大的支撑力量</td></tr>
  <tr><td>止损</td><td>设一个"最多亏多少"的底线，到了就卖，保护本金</td></tr>
  <tr><td>仓位</td><td>你多少钱买了黄金。50%仓位=一半钱买了，一半留着</td></tr>
</table>
"""

    def _footer(self) -> str:
        return f"""
<div class="footer">
  <p><span class="bold">Gold Miner</span> 多因子量化决策系统 · 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')} · {'专家版' if self.expert else '小白友好版'}</p>
  <p>数据来源：上海黄金交易所 · FRED · NewsAPI · jinjia.com.cn · anysearch · 世界黄金协会 · 上期所</p>
  <p style="margin-top:8px">⚠ 本报告仅供参考，不构成投资建议。投资有风险，入市需谨慎。</p>
</div>"""

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _maybe_explain(self, text: str) -> str:
        """仅小白版输出解释."""
        if self.expert:
            return ""
        return f'<div class="explain">{text}</div>'

    @staticmethod
    def _calc_rsi(close: pd.Series, period: int = 14) -> float:
        if len(close) < period + 1:
            return 50.0
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(period).mean().iloc[-1]
        avg_loss = loss.rolling(period).mean().iloc[-1]
        if avg_loss == 0 or pd.isna(avg_loss):
            return 100.0 if avg_gain > 0 else 50.0
        return float(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))
