# 公开快照：第三方版权及素材处理

本文件仅在公开副本中调整；原工作区及其声明不变。当前副本不含上游`.git`、数据库、环境或原始运行日志。

## 保留的来源及版权

| 内容 | 保留方式 |
|---|---|
| 原库存项目 | 完整 [LICENSE](../LICENSE)；Copyright (c) 2018 Gavin D'souza；[原仓库](https://github.com/gavindsouza/inventory-management-system)及基线d29d07109bc56b46c55e6d035e1cd5a53aaed123 |
| Bootstrap 4.1.3 | CSS和脚本的原文件头不变；补充 [官方许可证全文](licenses/BOOTSTRAP-4.1.3-LICENSE.txt)，来源为对应版本的官方仓库 |
| jQuery 3.3.1 | 原文件头不变；补充 [官方许可证全文](licenses/JQUERY-3.3.1-LICENSE.txt)，保留JS Foundation及贡献者声明 |
| Python依赖 | 通过requirements安装，不复制依赖源码或虚拟环境；许可证随相应安装包保留 |
| 8张新版演示截图 | Playwright真实浏览器生成，模拟商品标注可见；原图内容校验值与原工作区一致，不合成、不冒充本人手工验收 |

官方许可证来源：

- https://raw.githubusercontent.com/twbs/bootstrap/v4.1.3/LICENSE
- https://raw.githubusercontent.com/jquery/jquery/3.3.1/LICENSE.txt

## 已从公开副本排除

- `inventory/static/site_icon.png`
- `inventory/static/range_icon.png`
- `docs/util/1.gif`至`docs/util/12.gif`

这些文件存在于原项目，但逐文件公开权利尚未确认，本副本保守排除，未宣称已经取得授权，也不认定原项目侵权。原工作区文件及上游历史均未删除或改写；本副本不携带`.git`，因此不会通过历史分发这些文件。

原模板的Freepik/Flaticon署名注释仍保留作来源线索；favicon改为空数据引用，滑块不再引用旧图案，滑块功能及其他业务逻辑不变。原README的旧GIF嵌入在本副本改为上游链接，不重新托管或嵌入旧图片。

## 其他素材检查

副本的位图只有8张真实演示PNG。没有复制其他字体文件、图标包、GIF、音频、视频或不明图片。Bootstrap样式自带的图形属于该库的许可范围，原库声明完整保留。没有另行引入下载素材。

以上是本轮文件清单与来源核对，不是对所有第三方软件的独立法律鉴定。若以后打包依赖、增加素材或制作可执行文件，应再检查相应许可证。
