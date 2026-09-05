# build117 —— 发车时间 + 那条报错

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build117d-schedule.zip -d . && .venv/bin/python scripts/check_build.py && .venv/bin/python scripts/test_schedule.py && .venv/bin/python run_premarket.py --doctor
```

`144 项` + `14 项`。

---

# 第一件事：病因找到了

`--doctor` 在你机器上读到的：

```
launchd  com.crystal.cio.premarket.plist
  触发   周? 19:30（本机时间）
  **对不上**：本机盘前窗口是 06:00–09:15，而它排在 [19] 点
```

日志里三条：

```
跳过：2026-09-02 19:33 EDT …不在盘前窗口 06:00–09:15 内，不发
跳过：2026-09-03 19:40 EDT …
跳过：2026-09-04 19:30 EDT …
最后一份实际产出：CIO盘前情报简报+2026-09-02-0749.md
```

**19:30 触发 + 跑 19 分钟 = 09-01 那份 19:49 送达。分毫不差。**

## 19:30 是哪来的

```
A 股盘前窗口   07:00–09:15 北京
北京 07:30  =  UTC 23:30(前一天)  =  EDT 19:30(前一天)
```

这个 plist 是按 **A 股**盘前排的，只不过用机器的美东钟表达。当时它是对的；
`CIO_MARKET` 换成 `us` 之后没人动过它。

**一个写死的小时数记不住它是为哪个市场写的。**

（最后这句是推断。事实是 19:30，而北京 07:30 = 美东 19:30 这个换算是精确的。
我这一轮已经猜错三次——SPY 面板取短了、机器在北京时区、缓存混龄——
所以把推断和事实分开写。）

## 我之前的诊断错在哪

我说"机器在北京时区"。**方向反了**：机器一直在美东，
**是排程用北京的口径写的**。

## 更要紧的：问题已经从"发错时间"变成"什么都不发"

build107 加了时间闸之后，19:30 那一炮照常打、照常被拒，日志留一行"跳过"，
**而没有任何人会看那个日志**。

```
09-02 之前   每天在错的时间收到一份       ← 至少东西到手上了
09-02 之后   每天什么都收不到             ← 更难发现
```

**一个不发简报的早晨和一个没跑过的早晨长得一模一样。**

---

# 第二件事:不管病因是什么,这两道防线都不依赖我诊断对

## 一、绕过闸门是允许的,隐瞒绕过不允许

上次那次故障的要害**不是"发错了时间"**,是——

> **一份 19:49 发的简报,和一份 07:00 发的,长得一模一样。**

`--force` 继续可用(你想立刻要一份是正当需求),但用它在窗口外产出的简报,
会在**三条路上**都带标记:

```
Telegram 正文第一行   *CIO 盘前情报简报* — 2026-09-01 19:49 EDT　⚠窗口外
                      ⚠ **这份不是在盘前窗口内产出的**:…不在盘前窗口 06:00–09:15 内
caption               CIO 盘前简报 2026-09-01 19:49 EDT　⚠窗口外
存档文件名             CIO盘前情报简报+2026-09-01-1949+窗口外.pdf
```

**三处都要。** 这三处是简报被人看到的三条路——只标一处,另外两条上的那份
仍然和正点发的一样。归档里躺着的那份尤其要紧:三个月后没人记得
哪一份是凌晨正点发的、哪一份是晚上补跑的。

## 二、正确的排法(如果 doctor 说你没装或装错了)

```
bash scripts/install_launchd.sh
```

默认小时**从窗口现算**（美东盘前 06:00 起 → 装在 06:00），周一到周五，
夏令时 launchd 会自己跟。**装在窗口外会被拒绝**，见第四件事。

更稳的排法是让程序自己判断(cron 每小时敲一次门,窗口外几毫秒就退出):

```
0 * * * 1-5  cd ~/.openclaw/workspace/cio-agent && .venv/bin/python run_premarket.py
```

这样**一年两次夏令时切换不用管**,而写死小时数就得手改。

---

# 第三件事:那条报错

```
TypeError: unhashable type: 'dict'
```

它是 `ZoneInfo` 内部抛的,只讲"dict 不能做弱引用缓存的键"——
**说的是症状发生的地方,不是错误发生的地方**。签名上写着
`machine_tz: str | None`,但类型注解在运行时什么都不做,
错的参数一路走到最里层才炸。

现在:

```
>>> local_window(PREMARKET_WINDOW)
TypeError: machine_tz 要一个时区名字符串（如 'America/New_York'），收到的是 dict：{...}

>>> local_window("Nowhere/Nothing")
ValueError: 认不出这个时区名：'Nowhere/Nothing'（ZoneInfoNotFoundError）。
           要的是 IANA 名字，比如 'America/New_York' / 'Asia/Shanghai'
```

**是哪个参数、传的是什么、该传什么**——三样都说。
认不出的时区名故意抛 `ValueError` 而不是静默当 `None`:
静默当 `None` 会退回"用机器时区",那正是这一整条线要防的事。

---

# 第四件事:安装脚本不再写死小时数

```
默认小时      从 cio.schedule.local_window() 现算 —— 换市场它自己跟着换
窗口外        **拒绝安装**（真要装得显式 CIO_PREMARKET_ALLOW_ANY_HOUR=1）
算不出窗口     把 Python 的 stderr 原样印出来,再指向 --doctor
```

最后一条是写这一版时**自己撞上的**:`set -e` 下 `WIN=$(失败的命令)` 会
当场退出,我写的那句"算不出盘前窗口"永远打不出来——装的人只看到一个
**空白的 exit 1**。这正是这一整轮在防的形状,而它就长在防它的代码里。

---

# 变异测试:23 个,分三批

```
第一批 7 个    2 个漏网
第二批 6 个    4 个漏网
第三批 10 个   1 个漏网
```

**七个漏网,没有一个是因为断言写得不够狠,全是同一个毛病:我断的是文本。**

```
"mark" in src[i:i+120]        紧挨着的 `_market_stamp` 里就有 "mark" 这四个字母
"拒绝安装" in sh               变异只删 exit 1、留着那句 echo,照样绿
"local_window()" in sh         **文件开头我自己写的注释里就有这个词**
doctor 那三条同理              同样的字在模块注释里也有
_out_of_window 的所有断言       都是直接传 True 进去测的,写死成 False 全绿
```

改法只有一个方向：**断行为,不要断文本**。

```
caption            走 AST,要求那个 f-string 里真的引用了名为 mark 的变量
_out_of_window     走 AST,要求那个赋值同时引用 forced 和 in_window
安装脚本           **真的跑一遍**（假 HOME + launchctl/plutil 空壳）,
                   读它写出来的 plist,看 Hour 是不是 6、Weekday 是不是 1–5
doctor             **真的跑一遍**（假 HOME + 复刻她那个 19:30 的 plist）,
                   断它印出来的字
```

改完 23 个全被抓到。

顺带:把安装脚本真跑一遍这条,**第一次跑就抓出我自己的夹具 bug**——
软链解释器会丢掉 venv 的 site-packages（`No module named 'yaml'`）。
而抓到它的,正是刚加的"把 stderr 原样印出来"。

---

# 第五件事：那个 0xbc，我又猜错了

你第一次跑，红的是：

```
ERR   **安装脚本拒绝把任务排在窗口之外**
        UnicodeDecodeError: 'utf-8' codec can't decode byte 0xbc in position 62
```

我说是 locale 不是 UTF-8。**错的**——你的 `locale` 全是 `en_US.UTF-8`，
`getpreferredencoding` 返回 `UTF-8`。**这是这一轮第四次猜错。**

```
一  "SPY 面板取短了"           错   rs_mkt_samples=405 一眼否掉
二  照着一的错假设做的防护       白做  405 行上那个告警永远不响
三  "机器是北京时区 → 混龄"      错   date 一条命令否掉
四  "locale 不是 UTF-8"        错   locale 一条命令否掉
```

**每一次都是一条只读命令就能否掉的，而我每一次都先讲了故事。**

## 所以这次不讲故事，让它自己报

locale 既然是 UTF-8，那就是**输出里真的有一个非 UTF-8 的字节**。
我在这边逐字节数过我自己那份输出：第 62 字节是 `\xb8`（「一」的中段），
完全合法。**你那份在同一位置是 `\xbc`，所以你那份和我这份不一样**，
而我不知道为什么。

夹具现在会**当场把它印出来**，不管用例是红是绿：

```
[注意] stdout 在第 62 字节不是合法 UTF-8：b'\xbc'
[注意] 前后 48 字节：b'...'
[注意] 开头 120 字节：b'...'
```

**特意做成"绿了也印"**：加了 `errors="replace"` 之后用例会变绿，
如果只把字节塞进断言消息，它就再也没人看见了——
那正好又是一次"修好了报警、于是问题隐身"。

跑完把那三行 `[注意]` 发我（**没有的话就是它没再复现**）。

---

# 你上一次其实什么都没跑

```
unzip:  cannot find or open /Users/crystal/Downloads/cio-build117c-schedule.zip
```

zip 还没落到 Downloads，`&&` 在第一步就断了——**后面四条一条都没执行**。
先在对话里把文件下下来，再跑。

---

# 装完跑这四条

```
1  .venv/bin/python scripts/check_build.py       144 项
2  .venv/bin/python scripts/test_schedule.py      14 项
3  bash scripts/install_launchd.sh               ← **重装,小时数从窗口现算**
4  .venv/bin/python run_premarket.py --doctor    ← 核对,应该报 06:00 且不再"对不上"
```
