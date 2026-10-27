# sp2：Supervisor 的类 PM2 命令入口

`sp2` 只负责命令转换，调用本机现有 `supervisorctl`。需要 Python 3.8+ 和 Supervisor；进程定义、重启策略、日志和开机启动继续由现有 Supervisor 管理。

## 项目缘起

随着 AI 发现漏洞的能力不断增强，安全更新也越来越值得关注。从 PHP2Shell 到随后 Node.js 的漏洞消息，让我开始考虑：如果今后 Node.js 也需要更频繁地升级，依赖它的进程管理工具会不会带来额外的维护负担？PM2 一直用得很顺手，但它本身依赖 Node.js，升级运行环境时，往往还得兼顾 PM2 的安装与运行。

Supervisor 是一个合适的替代选择，但 `supervisorctl` 命令较长，操作习惯也与 PM2 不同。于是有了 `sp2`：在 Supervisor 之上封装一层简短、接近 PM2 使用习惯的命令入口，让日常进程管理更顺手，也减少进程管理工具对 Node.js 的依赖。

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

`sp2 ls` 显示的是 Supervisor 中已配置的进程；首次安装且没有进程配置时，列表可能为空。后文示例中的 `api`、`worker` 等名称需要替换为自己的 Supervisor 进程名。

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

## 命令对应关系

| sp2 | supervisorctl |
| --- | --- |
| 无参数、`help`、`--help` | 显示 sp2 使用帮助 |
| `ls`、`list`、`status [目标...]` | `status [目标...]` |
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

目标使用 Supervisor 进程名、`组:进程`、带引号的 `'组:*'`，或原生命令支持的 `all`。`logs` 只接受单个进程。Supervisor 的数字进程名仍是名称，不会转换成 PM2 ID。

## 行为约定

- 输出和退出码沿用 Supervisor：`status` 中存在停止的进程可能返回非零，连接失败或启停失败也会返回非零。
- `start api` 启动已注册的 api；首次添加进程仍需编写 Supervisor 配置并执行 `sp2 reload 组名`。
- `restart` 不读取配置。`reload` 是应用 Supervisor 配置变更，仅重启受影响的组；代码变了但配置没变时不会因此重启。它不是 PM2 cluster 的零停机 reload，也不会调用重启整个 daemon 的 `supervisorctl reload`。
- 日志默认跟随 stdout；`--err` 切换 stderr。若现有配置设置 `redirect_stderr=true`，错误输出也会进入 stdout。`--bytes` 是字节数，不提供含义不同的 `--lines` 参数。
- 不提供 PM2 的 `delete/save/resurrect/startup/scale`。原生 `remove` 只移除运行时注册，不能当作永久删除磁盘配置；需要时明确使用 `sp2 ctl remove 组名`。
- `sp2` 命令本身不自动启动 Supervisor、安装依赖、修改配置或申请 sudo；只有显式 `--sudo` 才调用 sudo。安装脚本的依赖安装行为见上文。`flush` 会清空目标日志。

## 验证

```bash
bash -n install.sh
python3 -m unittest discover -s tests
```

测试会启动隔离的临时 Supervisor，实际验证命令执行、配置更新、日志和错误退出码，不连接机器上已有的服务。

安装脚本测试使用临时目录和模拟包管理器验证 macOS / Ubuntu 分支，不安装系统软件。
