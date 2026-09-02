"""CIO 信息处理中心 —— 数据资产化飞轮 (Build 1)。

模块概览：
  config       读取 config/*.yaml 与 .env
  models       Pydantic 数据模型（统一 typed schema）
  db           SQLite 元数据/审计（documents/sources/watchlist_hits/briefs/collection_log）
  ollama_client 本地 Ollama 推理/向量（严禁云端；支持 CIO_MOCK_LLM 离线自测）
  vectorstore  LanceDB + nomic-embed 语义检索
  collect      采集：RSS / yfinance 行情 / EDGAR 公告
  funds        资金面：北向/ETF/板块净流入（akshare，全程降级容错）
  classify     趋势视角打标（资金面/政策/预期修正/异动/公告）+ 关注池命中
  process      清洗/去重/翻译摘要/切块/向量化 → Company Archive
  brief        盘前早报编撰（Layer1 趋势信号 + Layer2 十大新闻，中英对照）
  render       结构化对象 → Markdown + PDF（reportlab 内置中文字体）
  deliver      Telegram sendMessage + sendDocument（不与 OpenClaw 抢消息）
  topic        专题报告（个股 + 主题；方向性问题礼貌拒答）
"""

__version__ = "1.0.0-build1"
