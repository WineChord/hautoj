# HAUTOJ

河南工业大学 HAUTOJ 新生周赛题解与算法竞赛训练资料。

站点提供两条互相贯通的学习路径：

- 按 2016—2025 年新生周赛从新到旧复盘，保留每场原始题号顺序。
- 将 526 道唯一题重组为 10 个知识模块，按难度训练。

每道题均有独立规范页、可复用折叠摘要、原题与周赛链接、零基础先修、思路推导、正确性、复杂度、易错点、公开样例和完整 C++17 实现。参考代码通过编译检查；样例状态按题逐一标注。

## 本地构建

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --requirement requirements.txt
.venv/bin/python scripts/generate_site.py
.venv/bin/python scripts/check_content.py
.venv/bin/python scripts/check_cpp.py
.venv/bin/python scripts/check_links.py
.venv/bin/python scripts/check_rendering.py
.venv/bin/python -m mkdocs build --strict
```

本地预览：

```bash
.venv/bin/python -m mkdocs serve
```

## 资料性质

本仓库由公开页面与公开参考资料独立整理，不代表河南工业大学、学院或校队。题目名称、题面与原始素材的权利归各自作者和平台所有；具体通知和规则以正式渠道为准。

公开站点：[www.wineandchord.com/hautoj](https://www.wineandchord.com/hautoj/)
