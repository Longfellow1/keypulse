# 首次配置 · KeyPulse Setup

KeyPulse 不需要配置大模型。装完之后要做的只有两件事：**授予 macOS 权限**，然后**确认 daemon 在跑**。

---

## 1. 启动并授权

```bash
keypulse install init
```

按菜单栏 HUD 的黄条提示，到系统设置里给 KeyPulse 授权（辅助功能、屏幕录制）。首次安装或重新打包后，也可以运行：

```bash
keypulse install onboard   # 引导授予 macOS 权限
```

---

## 2. 确认在运行

```bash
keypulse status        # daemon 状态与采集运行信息
keypulse healthcheck   # daemon 进程 + 事件流活性
keypulse doctor        # 诊断安装 / 配置 / 权限问题
```

如果需要手动管理 launchd（开机自启、崩溃自动拉起）：

```bash
keypulse install launchd
```

`.app` / `make install` 只作为本地开发打包路径。

---

## 隐私与数据

配置文件在 `~/.keypulse/config.toml`，数据库在 `~/.keypulse/keypulse.db`。全部数据留在本机，无云端同步、无网络请求。
