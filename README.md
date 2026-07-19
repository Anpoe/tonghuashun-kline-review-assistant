# 同花顺 K线复盘助手

自动记录同花顺远航版“新K线训练营”的开始盘面与结束盘面，生成拼接截图，并保存为 Markdown 复盘笔记。截图与 OCR 识别均在本机完成。

> 本项目是第三方开源工具，并非同花顺官方产品，与浙江核新同花顺网络信息股份有限公司没有隶属或合作关系。“同花顺”是其权利人的商标。

## 下载

从 [GitHub Releases](https://github.com/Anpoe/tonghuashun-kline-review-assistant/releases/latest) 下载最新版：

- `KlineReviewAssistant-Setup-1.1.1.exe`：安装版，推荐普通用户使用。
- `SHA256SUMS.txt`：发布文件校验值。

仓库源码本身就是可直接运行的版本，因此不再重复发布 portable ZIP。下载或克隆仓库后双击 `start_recorder.bat` 即可；缺少运行依赖时脚本会自动创建项目内的 `.venv`。

程序尚未进行代码签名，因此 Windows SmartScreen 可能在首次运行时显示提醒。

## 功能

- 自动查找并启动同花顺远航版，找不到时可以手动选择 `happ.exe`。
- 自动识别训练营首页、`30/30` 开始盘面、最后盘面和结果页。
- 保存开始与结束两张盘面，并以间隔和重复区域提示进行拼接。
- 保存 K 线、成交量、大单净量区域以及结果卡片。
- 自动生成包含股票、代码、训练区间、收益率和图片的 Obsidian Markdown 笔记。
- 内置本地数据看板，展示训练局数、平均收益、胜率、最佳单局和收益趋势。
- 支持按时间、股票和结果筛选历史记录，并查看每局完整盘面与结果卡片。
- 悬浮窗显示当前识别、截图和保存进度。
- 支持窗口缩放、不同 DPI 和多显示器。

## 首次使用

1. 安装并启动程序。
2. 首次设置会自动寻找同花顺远航版；没有找到时请选择 `happ.exe`。
3. 选择复盘笔记保存文件夹，可以是 Obsidian 仓库内的目录或普通文件夹。
4. 点击“保存并开始”，然后打开“新K线训练营”进入 K 线训练。

悬浮窗底部点击“数据看板”即可在浏览器中查看表现与历史。看板仅监听本机 `127.0.0.1`，不会上传复盘记录或图片。

设置保存在 `%APPDATA%\KlineReviewAssistant\config.yaml`。升级或卸载程序不会删除复盘笔记，也不会覆盖已有设置。

## 工作流程

1. 识别到训练盘面的 `30/30`。
2. 确认顶部行情已出现，再等待 1.5 秒保存开始盘面。
3. 最后一根 K 线出现后保存结束盘面。
4. 在结果页通过“股票区间涨幅”定位结果卡片并读取股票信息。
5. 生成拼接图片、结果卡片和 Markdown 笔记。
6. 返回训练营首页后重置本局缓存。

## 系统要求

- Windows 10/11 64 位
- 同花顺远航版
- “新K线训练营”小程序

## 本地构建

本地使用始终运行当前源码，修改完成后无需重新安装：

```powershell
.\start_recorder.bat
```

首次准备独立 Python 环境也可以手动运行：

```powershell
.\setup_local.bat
```

生成 GitHub 安装包时再安装构建依赖：

```powershell
python -m pip install -r requirements-dev.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_release.ps1
```

构建结果位于 `release`，只包含安装程序和 `SHA256SUMS.txt`。安装器需要 Inno Setup 6；构建完成后默认删除 `build` 和 `dist` 中间目录，传入 `-KeepBuildArtifacts` 可以保留它们用于调试。

发布构建只打包通用的 `config.default.yaml`，不会打包开发者本机的 `config.yaml`。

## 自检

```powershell
python -m unittest test_dashboard.py test_recorder_detection.py
```

发布脚本还会自动运行 `smoke_test_release.py`，验证打包后的 OCR、数据看板、首次设置窗口和正常悬浮窗，不会修改当前用户配置。

## License

[MIT](LICENSE)
