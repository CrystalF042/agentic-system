# build86 —— 真机第一份 PDF 露出的两个问题 + 定时改到美东早上

## 真机验证结果：快照本身完全正确

11:04 ET 那份 PDF 里：

```
标普500期货  7,684.75  -0.48%  实时
纳指100期货  29,402.25 -0.30%  实时
日经225     66,254.44 -0.23%  今日 02:25
恒生指数    25,566.99 -0.07%  今日 04:05
欧洲斯托克50 6,436.43  -0.76%  实时
```

期货实时、亚洲显示几小时前、欧洲实时 —— **新鲜度按实测年龄归档，在真实数据上完全对。**
10 个符号全部可取，不用换。

## 修的两个问题

**一、`**` 原样印在了 PDF 上。**

报告上出现的是：

```
下表每一项都标注了 ** 该数字自己的时间 **——
```

`render_note()` 的文本要同时进 md、reportlab PDF、HTML→PDF **三个渲染器**，
而后两个不解析 markdown。**给三处共用的文本只能用纯文本。**
（这和 Telegram 摘要那次是同一类：一段文字在一个渲染器里是强调，在另一个里是字面量。）

**二、HTML 渲染器把原始时间戳吞了。**

md 版印的是 `[08-31 11:02] 实时`，HTML 版只印了 `实时`。

只印"实时"是一个**结论**；印上 `[08-31 11:02]` 才是可核对的**事实**——
读者能自己判断这个"实时"到底有多实时。整个模块的立意就是"每个数字带自己的时间"，
结果最终交付的那个渲染器把时间去掉了。

## 定时任务：19:30 → 07:00 ET，周一至周五

你的 plist 有个好消息：`ProgramArguments` 用的是 `.venv/bin/python`，
解释器没问题（否则定时跑会撞上和你终端里一样的依赖缺失，而且没人看得到报错）。

问题只在 `Hour 19`。附带的 `com.crystal.cio.premarket.plist` 已改好：

```
Hour 19 → 7        美股 09:30 开盘前 2.5 小时
无 Weekday → 周一至周五
```

**一个容易踩的坑**：`StartCalendarInterval` 要指定多个星期几，
必须写成**一组 dict 的数组**。在一个 dict 里塞多个 `Weekday` 只会生效最后一个，
**而且不报错**——任务照常注册、照常运行，只是一周只跑一天。

安装：

```
cp com.crystal.cio.premarket.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.crystal.cio.premarket.plist
launchctl load ~/Library/LaunchAgents/com.crystal.cio.premarket.plist
launchctl list | grep cio
```

## 自检

`scripts/check_build.py`：60 项。
另外 `check_build.py` 本身也修了——用错解释器时会说「用错了 Python」并退出，
不再谎报「文件没有真正落到 src/cio/ 下」。
