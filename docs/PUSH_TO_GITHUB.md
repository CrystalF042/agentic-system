# 推到 GitHub —— 一步一步

> 全程在你 Mac 的终端里做。先 `cd ~/.openclaw/workspace/cio-agent`。
> 下面每一段都可以整段粘贴（**没有 `#` 开头的行**）。

---

## 先说清楚风险在哪

这件事和这套系统里其他所有事都不一样：

```
别的错   下一版修掉就行
这个错   推出去的那一刻就不可撤销
```

**git 有历史。一个文件一旦被 commit 过，后来删掉它不会把它从仓库里去掉。**

你这个目录里同时躺着三样东西：

| | 里面是什么 | 泄漏之后 |
| --- | --- | --- |
| `.env` | Telegram token（还有以后要加的 Anthropic key） | 别人能控制你的 bot、花你的钱 |
| `*.db` | 账本：持仓、成本、每一笔交易 | 你的实际仓位公开 |
| `raw-data/` `memory/` 等 | 论点台账、复核台账、信号卡片 | 你的研究过程公开 |

`.gitignore` 已经把它们全都排除了（那份文件开头就写着这段警告）。
**但"写了规则"和"规则确实拦住了"是两件事** —— 所以先跑一道闸。

---

## 第 0 步：装上带闸门的这一版

```
cd ~/.openclaw/workspace/cio-agent && unzip -o ~/Downloads/cio-build124b-engine.zip -d . && .venv/bin/python scripts/check_build.py
```

要看到 `全部 151 项通过`。

---

## 第 1 步：看看现在是什么状态

```
cd ~/.openclaw/workspace/cio-agent && git status
```

两种可能：

**A. 报错 `not a git repository`** —— 还没初始化，走第 2 步。

**B. 印出一堆文件** —— 已经是仓库了，**跳过第 2 步**，直接去第 3 步。

---

## 第 2 步（只有 A 才做）：初始化

```
cd ~/.openclaw/workspace/cio-agent && git init && git branch -M main
```

**顺序很要紧**：`.gitignore` 已经在了（zip 里带的），所以 `git init` 之后
第一次 `git add` 就会自动跳过 `.env` 和账本。反过来做的话，
第一次 add 就把它们收进去了。

---

## 第 3 步：跑闸门（**这一步是重点**）

```
cd ~/.openclaw/workspace/cio-agent && python3 scripts/git_preflight.py
```

它查五件事：

```
[1] .gitignore 拦住了该拦的没有        拿 7 个探针真的问一遍 git
[2] 这次会进仓库的文件里有没有不该进的   .env / *.db / raw-data/ ...
[3] 那些文件的内容里有没有像密钥的字符串  .gitignore 对了，也可能有人把 key 粘进了 README
[4] **历史**里有没有提交过这些东西      工作区干净 ≠ 历史干净
[5] 有没有大文件                       不致命，只是会让每次 clone 变慢
```

**红了就停在这里，别往下走。** 每一条 NG 后面都写了怎么处理。

第 4 条如果红了，情况最特殊：**删文件是没用的**，
要做的是换掉所有出现过的密钥、然后删掉仓库重建一个。

想先看看到底哪些文件会被推上去：

```
cd ~/.openclaw/workspace/cio-agent && python3 scripts/git_preflight.py --list
```

---

## 第 4 步：确认 GitHub 上那个仓库

浏览器打开 https://github.com/CrystalF042/agentic-system

**看不到（404）有两种可能**：仓库是 private（正常），或者还没建。

还没建的话：GitHub 右上角 **+** → **New repository** →
名字填 `agentic-system` → **选 Private** →
**不要**勾 "Add a README"（勾了会让第一次 push 冲突）→ Create。

> 建议选 **Private**。这个仓库里有你的研究方法、闸门阈值、
> 系统架构。就算密钥都拦住了，那些也是你的东西。

---

## 第 5 步：告诉 git 推去哪

先看有没有设过：

```
cd ~/.openclaw/workspace/cio-agent && git remote -v
```

**没有任何输出** → 加上：

```
cd ~/.openclaw/workspace/cio-agent && git remote add origin https://github.com/CrystalF042/agentic-system.git
```

**已经有 origin 但地址不对** → 改掉：

```
cd ~/.openclaw/workspace/cio-agent && git remote set-url origin https://github.com/CrystalF042/agentic-system.git
```

---

## 第 6 步：登录（第一次才要）

Mac 上最省事的是 GitHub CLI。先看装没装：

```
gh --version
```

**装了** → 登录一次就行，以后不用管：

```
gh auth login
```

选 `GitHub.com` → `HTTPS` → `Yes`（用 git 凭据）→ `Login with a web browser`，
把屏幕上那串码粘到浏览器里。

**没装** → 两条路，任选：

装一个（推荐，用 Homebrew）：

```
brew install gh && gh auth login
```

或者不用 gh，用密码框里粘 token：
GitHub → 右上角头像 → Settings → Developer settings →
Personal access tokens → **Tokens (classic)** → Generate new token →
勾 `repo` → 生成 → **复制那串 `ghp_...`**（只显示一次）。
下一步 push 时用户名填 `CrystalF042`，密码栏**粘那串 token**（不是你的 GitHub 密码）。

---

## 第 7 步：提交并推上去

再跑一次闸门确认还是绿的，然后一口气推：

```
cd ~/.openclaw/workspace/cio-agent && python3 scripts/git_preflight.py && git add -A && git status
```

`git status` 会列出这次要提交的东西 —— **花十秒扫一眼**，
确认里面没有 `.env`、没有 `.db`、没有 `raw-data/`。

看着没问题就：

```
cd ~/.openclaw/workspace/cio-agent && git commit -m "build124: 自动流水线 Build 1-5 + 辩论引擎可切换 + 推送前闸门" && git push -u origin main
```

如果它说 `updates were rejected`（远端已经有东西了，比如你建仓库时勾了 README）：

```
cd ~/.openclaw/workspace/cio-agent && git pull --rebase origin main && git push -u origin main
```

---

## 第 8 步：推完之后，去看一眼

浏览器打开仓库，做三件事：

1. **翻一遍文件列表** —— 有没有 `.env`、`.db`、`raw-data/`？
2. 右上角搜索框搜 `sk-ant` 和 `TELEGRAM_BOT_TOKEN`，
   确认只搜到文档里的占位符，没有真值
3. 确认 `.gitignore` 在，而且内容对

**万一真的推上去了密钥** —— 不要只是删文件再 commit。按这个顺序：

```
1  立刻去 @BotFather → /mybots → 你的 bot → API Token → Revoke current token
2  Anthropic 后台把那个 key 删掉，重新生成一个
3  GitHub 上把这个仓库删掉（Settings → 最下面 Danger Zone → Delete）
4  重新建一个，先确认 .gitignore 在，再 git init，再跑一次闸门
```

**第 1、2 步比第 3 步紧急。** 仓库删了，别人可能已经抓走了。

---

## 以后每次推

```
cd ~/.openclaw/workspace/cio-agent && python3 scripts/git_preflight.py && git add -A && git commit -m "说清楚这次改了什么" && git push
```

闸门红了 `&&` 就断了，后面的不会跑 —— **这是故意的**。

---

## 附：这道闸自己也差点没判别力

写完第一版我拿一个假 key 去试，它**没红**。

查下来是我自己的占位符名单里有 `"abcdef"`，而那个假 key 末尾恰好是
`...abcdefghij` —— 于是**整行被当成占位符放过了**。
一条真 key 里出现 `abcdef` 一点都不稀奇（base62 就那几十个字符）。

改法：占位符只拿来比对**匹配到的那一段**，不比对整行。

记在这里是因为它是这套系统里最常见的那个形状：
**一道从来不红的闸，和一道不存在的闸，是同一回事。**
