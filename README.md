# sp2：Supervisor 的类 PM2 命令入口

`sp2` 为本机现有 Supervisor 提供类 PM2 命令、带稳定 ID 的进程组表格，以及按 ID 或名称软删除配置的操作。需要 Python 3.8+ 和 Supervisor；进程定义、重启策略、日志和开机启动继续由 Supervisor 管理。

## 项目缘起

随着 AI 发现漏洞的能力不断增强，安全更新也越来越值得关注。从 PHP2Shell 到随后 Node.js 的漏洞消息，让我开始考虑：如果今后 Node.js 也需要更频繁地升级，依赖它的进程管理工具会不会带来额外的维护负担？PM2 一直用得很顺手，但它本身依赖 Node.js，升级运行环境时，往往还得兼顾 PM2 的安装与运行。

Supervisor 是一个合适的替代选择，但 `supervisorctl` 命令较长，操作习惯也与 PM2 不同。于是有了 `sp2`：在 Supervisor 之上封装一层简短、接近 PM2 使用习惯的命令入口，让日常进程管理更顺手，也减少进程管理工具对 Node.js 的依赖。

## 支持的系统

目前支持以下系统：

| 系统 | 依赖安装方式 |
| --- | --- |
| macOS | 使用 Homebrew 安装缺失的依赖 |
| Ubuntu | 使用 apt 安装缺失的依赖 |

运行需要 Python 3.8+ 和 Supervisor。其他 Linux 发行版和 Windows 暂不在支持范围内。

## 安装（macOS / Ubuntu）

### 1. 准备 Git 和包管理器

macOS：先安装 [Homebrew](https://brew.sh/)，按安装提示配置 PATH，并重新打开终端。确认以下命令可用；如果尚未安装 Git，可运行 `brew install git`：

```bash
brew --version
git --version
```

Ubuntu：先安装 Git（已安装可跳过）：

```bash
sudo apt-get update
sudo apt-get install -y git
```

### 2. 克隆仓库并安装

在终端切换到想保存源码的目录，然后执行：

```bash
git clone https://github.com/leetesla/sp2.git
cd sp2
./install.sh
```

脚本检查 Python 3.8+、`supervisorctl` 和 `supervisord`，只补装缺失的依赖：macOS 使用 Homebrew，Ubuntu 使用 apt，需要管理员权限时调用 sudo。直接以普通用户运行 `./install.sh` 即可。

默认将 `sp2` 的独立副本安装到 `~/.local/bin/sp2`，移动仓库不会影响已安装命令。Ubuntu 安装 Supervisor 软件包时，系统包管理器可能同时启动服务；脚本本身不会额外启动服务或修改 Supervisor 配置。

### 3. 配置 PATH 并验证安装

先让当前终端可以找到命令：

```bash
export PATH="$HOME/.local/bin:$PATH"
sp2 --help
sp2 --dry-run restart api
```

最后一条命令应输出 `supervisorctl restart api`，只显示转换结果，不会实际重启进程。

为使新终端也能找到 `sp2`，将以下配置添加到 shell 配置文件（已存在时无需重复添加）：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

macOS 默认 Zsh 使用 `~/.zshrc`，Ubuntu 默认 Bash 使用 `~/.bashrc`。保存后重新打开终端即可；使用其他 shell 时，请修改对应的配置文件。

### 4. 连接 Supervisor

macOS 使用 Homebrew 默认配置时，在当前终端执行：

```bash
export SP2_CONFIG="$(brew --prefix)/etc/supervisord.conf"
# Supervisor 尚未运行时启动
brew services start supervisor
sp2 ls
```

Ubuntu 使用系统默认配置时，在当前终端执行：

```bash
export SP2_CONFIG=/etc/supervisor/supervisord.conf
# Supervisor 尚未运行时启动
sudo systemctl start supervisor
sp2 --sudo ls
```

将对应的 `export SP2_CONFIG=...` 行加入 `~/.zshrc` 或 `~/.bashrc`，即可在新终端继续使用。已有自定义 Supervisor 配置时，请使用自己的配置路径，并按原有方式管理服务。Ubuntu 如需开机启动，可执行 `sudo systemctl enable supervisor`。

`sp2 ls` / `sp2 ps` 按进程组显示表格并分配稳定 ID；首次安装且没有进程配置时，表格只有表头。后文示例中的 `api`、`worker` 等名称需要替换为自己的 Supervisor 进程或组名。

### 自定义安装与更新

在仓库目录运行以下命令，可自定义安装位置，或禁止自动安装依赖：

```bash
./install.sh --prefix /usr/local  # 安装到 /usr/local/bin/sp2，按需使用 sudo
./install.sh --skip-deps         # 只检查依赖，缺失时直接报错
./install.sh --help
```

自定义目录时，请将对应的 `目录/bin` 加入 PATH；`--prefix` 必须使用绝对路径。

更新时进入之前克隆的 `sp2` 仓库目录，拉取代码并重新安装：

```bash
git pull --ff-only
./install.sh
```

如果首次安装时指定了 `--prefix`，更新时使用相同参数。重新安装会替换目标位置已有的 `sp2` 文件或软链接。

## 使用

执行 `sp2`、`sp2 help` 或 `sp2 --help` 均显示命令使用帮助；查看进程状态请使用 `sp2 ls`。查看单个命令的参数可使用 `sp2 logs --help` 等形式。

也可以在仓库根目录直接运行，无需全局安装：

```bash
./sp2
./sp2 help
./sp2 --dry-run restart api
```

### 连接与常用操作

指定现有 Supervisor 的主配置，或沿用 supervisorctl 自己的默认配置发现规则：

```bash
export SP2_CONFIG=/etc/supervisor/supervisord.conf
# Homebrew 示例：export SP2_CONFIG="$(brew --prefix)/etc/supervisord.conf"
sp2 ls
sp2 start api
sp2 restart api worker
sp2 stop 'worker:*'
sp2 logs worker:worker_00
sp2 logs api --err
sp2 logs api --nostream --bytes 4096
```

连接选项放在子命令前，也可以单次指定配置、URL 或 sudo：

```bash
sp2 -c /path/to/supervisord.conf status
sp2 -s unix:///path/to/supervisor.sock ls
sp2 --sudo -c /etc/supervisor/supervisord.conf restart api
sp2 --dry-run restart 'worker:*'
```

`SP2_SUPERVISORCTL` 可指定 supervisorctl 可执行文件的完整路径，不接受额外参数。

### 表格与稳定 ID

`sp2 ps`、`sp2 ls` 和 `sp2 list` 无目标参数时，以表格显示组 ID、名称、状态、进程数、PID、内存占用和运行时间。例如：

```text
┌────┬─────────────────┬─────────┬───────────┬────────┬──────────┬─────────┐
│ id │ name            │ status  │ processes │ pid    │ memory   │ uptime  │
├────┼─────────────────┼─────────┼───────────┼────────┼──────────┼─────────┤
│ 0  │ cogvideo-worker │ RUNNING │ 1         │ 119185 │ 15.0 MiB │ 0:20:44 │
│ 1  │ laravel-worker  │ STOPPED │ 1         │ -      │ 0 B      │ -       │
└────┴─────────────────┴─────────┴───────────┴────────┴──────────┴─────────┘
```

Supervisor 按组移除，因此一行和一个 ID 对应整个进程组；同组有多个进程时会显示数量和全部 PID。`sp2 status`、`sp2 ls 目标...` 和 `sp2 ctl status` 保留原生逐进程状态输出。

`memory` 通过 macOS / Ubuntu 自带的 `ps` 读取各 PID 的 [RSS](https://www.man7.org/linux/man-pages/man1/ps.1.html)，按组汇总并显示 KiB / MiB / GiB。统计范围是 Supervisor 直接管理的进程；共享页可能重复计入，不包含未单独托管的子进程。已停止的组显示 `0 B`；远程连接、权限不足或进程在采样前退出时显示 `-`，内存采样失败不会影响列表使用。本机内存采样要求能识别对应的 Unix socket 连接。

ID 自动保存在当前用户的 `~/.local/sp2/ids.json`，macOS 与 Ubuntu 使用相同的相对主目录路径；例如 Linux root 用户为 `/root/.local/sp2/ids.json`。无需数据库或手动维护。不同 Supervisor 连接分别保存映射，同名组恢复后沿用原 ID，旧 ID 不会分配给其他名称；写入使用文件锁和原子替换。文件损坏时会报错并保留原文件。

`sp2 --sudo` 仍使用当前用户的 ID 文件；`sudo sp2` 则使用 sudo 后用户的主目录。查看列表和按 ID 删除时，请使用同一用户及相同连接。ID 目前用于 `del/delete`；其他命令继续接受 Supervisor 名称。

### 按 ID 或名称软删除

直接查看 ID 并删除即可；`del` 沿用 supervisorctl 的配置查找顺序，自动识别 Homebrew 安装前缀、当前目录及 Ubuntu 的标准配置位置。自定义位置也可通过 `-c` 或 `SP2_CONFIG` 指定：

```bash
sp2 ps
sp2 --dry-run del laravel-worker  # 查看配置改名和组更新计划
sp2 del laravel-worker           # 按组名删除
# 或：sp2 del 1                  # 按上方列表中的实际 ID 删除
# 多组：sp2 del laravel-worker veovideo-worker
```

以 `laravel-worker.conf` 为例，`del` 将配置改名为 `laravel-worker.conf.sp2.disabled`，再通过 Supervisor 的 `update 组名` 停止并移除该组。同名 `.sp2.disabled` 副本会被直接覆盖，只保留本次删除的配置。代码、日志和 ID 映射继续保留；正常 reload 或 Supervisor 重启后，该配置也不会重新加载。`delete` 是 `del` 的别名。

如果配置之前已被删除、移出 include 范围或加了禁用后缀，但组还留在 Supervisor 的运行时列表中，`del` 会直接按组更新并清理残留注册，无需重新创建配置文件。最终会检查目标是否已从列表中移除；若服务端仍有有效配置，会报错并显示本次使用的主配置路径，便于核对。

名称使用表格中的组名，即原生 `组:进程` 中冒号前的部分。纯数字参数按 ID 解释；数字组名用 `sp2 del name:123`。所有目标都会先检查，再开始修改配置。软删除支持已找到的主配置对应的本机 Unix socket；配置须位于主配置 include 引入的独立文件中。若文件还包含未选择的组或其他配置，会要求先拆分；若 include 使用 `*` 等仍会匹配禁用副本的规则，会要求先收窄为 `*.conf` 等规则。需要权限时使用 `sp2 --sudo del 组名`；若连配置都无法读取，请以有权限的用户执行。

恢复时，将 `.sp2.disabled` 文件改回原名，再应用配置；先确认原名位置没有新的配置：

```bash
mv -i /etc/supervisor/conf.d/laravel-worker.conf.sp2.disabled /etc/supervisor/conf.d/laravel-worker.conf
sp2 reload laravel-worker
sp2 ps
```

配置文件位置以实际输出为准；macOS 同样操作对应 Homebrew 配置目录中的文件。恢复后是否自动启动由原配置的 `autostart` 决定。

## 命令对应关系

| sp2 | supervisorctl |
| --- | --- |
| 无参数、`help`、`--help` | 显示 sp2 使用帮助 |
| `ls`、`list`、`ps`（无目标） | `status`，按组显示表格并维护 ID |
| `ls/list/ps/status [目标...]` | `status [目标...]`；`status` 始终使用原生输出 |
| `del/delete ID或组名...` | 配置加 `.sp2.disabled` 后缀，再 `update 组名...` |
| `describe/show/info 目标...` | `status 目标...` |
| `start/stop/restart 目标...` | 同名命令 |
| `pid [目标...]` | `pid 目标...`，省略目标时为 `pid all` |
| `logs 进程` | `tail -f 进程 stdout` |
| `logs 进程 --err` | `tail -f 进程 stderr` |
| `logs 进程 --nostream [--bytes N]` | `tail [-N] 进程 stdout` |
| `flush [目标...]` | `clear 目标...`，省略目标时为 `clear all` |
| `reload/update [组名...]` | `update [组名...]` |
| `reread` | `reread` |
| `sendSignal SIGUSR1 目标...` | `signal SIGUSR1 目标...` |
| `ctl 原生命令 参数...` | 原样转交 |

除 `del/delete` 外，目标使用 Supervisor 进程名、`组:进程`、带引号的 `'组:*'`，或原生命令支持的 `all`。`logs` 只接受单个进程。这些命令中的数字进程名仍是名称，不会转换成 ID。

## 行为约定

- `ls/ps/list` 无目标时格式化为组表格，状态退出码仍沿用 Supervisor：存在停止的进程可能返回非零。其他原生命令沿用其输出和退出码；软删除遇到错误返回非零。
- `start api` 启动已注册的 api；首次添加进程仍需编写 Supervisor 配置并执行 `sp2 reload 组名`。
- `restart` 不读取配置。`reload` 是应用 Supervisor 配置变更，仅重启受影响的组；代码变了但配置没变时不会因此重启。它不是 PM2 cluster 的零停机 reload，也不会调用重启整个 daemon 的 `supervisorctl reload`。
- 日志默认跟随 stdout；`--err` 切换 stderr。若现有配置设置 `redirect_stderr=true`，错误输出也会进入 stdout。`--bytes` 是字节数，不提供含义不同的 `--lines` 参数。
- `del/delete` 软删除会覆盖已有同名禁用副本，并停止目标组中的全部进程。原生 `sp2 ctl remove 组名` 只移除运行时注册，配置仍会被重新加载。不提供 PM2 的 `save/resurrect/startup/scale`。
- `sp2` 命令本身不自动启动 Supervisor 或安装依赖；列表操作维护本地 ID 文件，`del/delete` 修改配置文件名，`flush` 清空目标日志。只有显式 `--sudo` 才调用 sudo。安装脚本的依赖安装行为见上文。

## 验证

```bash
bash -n install.sh
python3 -m unittest discover -s tests
```

测试会启动隔离的临时 Supervisor，实际验证命令执行、配置更新、日志和错误退出码，不连接机器上已有的服务。

安装脚本测试使用临时目录和模拟包管理器验证 macOS / Ubuntu 分支，不安装系统软件。
