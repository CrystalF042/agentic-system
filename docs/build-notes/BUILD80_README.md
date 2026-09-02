# build80 —— Evidence Gate 扫描：几秒钟知道今天该跑哪几只

## 为什么

NVDA 和 AVGO 连着两只都是 `INSUFFICIENT`（3 条材料实质 0 条）。
**闸门没坏，是今天这两只确实没有增量事实。** AVGO 那条
"earnings triggered a sell-off, wiping $1.3 trillion off chip stocks"
被判成「背景」也是对的：那是**价格事实**，不是关于这家公司的新信息。
把大幅波动算成实质材料，等于允许一部在股价一动的时候就编一个新论点——
而 Evidence Gate 存在的全部理由就是拦住这件事。

问题在流程：逐只跑完整的一部，每只几分钟，多数在闸门那里被拦下，
**时间花在了"确认今天没新闻"上**。

## `run_scan.py`

```
CIO_MARKET=us python run_scan.py NVDA AVGO AMD MU TSM AMAT LRCX ARM
CIO_MARKET=us python run_scan.py NVDA AVGO --verbose      逐条列材料与判定理由
```

**一次模型都不调**——只做一部完整流程的前两步：采集材料、过实质度闸门。

```
AVGO     ✅ 跑一部                材料充分（4 条材料，实质 3）
NVDA     — 不跑                  无实质材料（2 条材料，实质 0）

**建议跑完整一部的标的**（按实质材料条数排序）：
  AVGO     SUFFICIENT   实质 3/4 条　材料充分

CIO_MARKET=us python run_unit_a.py "AVGO"
```

退出码：有任何一只达到 THIN 以上返回 0，全部 INSUFFICIENT 返回 1，
可以串成 `python run_scan.py ... && python run_unit_a.py ...`。

**关键是它用的是同一份采集代码和同一个闸门**（`unit_a.collect_materials`
+ `material_gate.assess`），不是另写一套近似规则。两套规则一定会漂移，
漂移之后没人知道该以哪个为准。

为此把 `build_unit_a` 里的采集段原样抽成 `unit_a.collect_materials(text)`，
逻辑一字未改，`build_unit_a` 改为调用它。自检用 AST 断言这一段里
没有任何模型调用，且 `build_unit_a` 确实走的是抽出来的这份。

## 一个已知的保守偏差

闸门是**非对称**的：拿不准就判「不实质」。所以
"AMD signed a multi-year supply agreement with a major cloud provider"
这类**有已发生动作、但没有金额锚点也不属于已登记事件类型**的材料会落到「背景」。
这是有意的设计（宁可漏过，不可放进一个假证据），但它意味着
**「无实质材料」偶尔的真实含义是「有材料，只是没匹配上规则」**。

处理方式：**观察到真实漏判时再放宽 `_EVENT` 列表，不要为了让演示出结果而放宽。**
用 `--verbose` 看逐条判定理由，漏判会当场看见。

## 自检

- `scripts/check_build.py`：48 项（新增 2 项 build80）
- `scripts/test_unit_a.py`：新增 7 项
