---
name: interview-post-extractor
description: Retrieve and summarize interview-experience posts through the interview-crawl MCP server. Use when the user provides a supported interview-post URL, including Nowcoder and Xiaohongshu, and wants the interview questions.
---

# 面经抓取整理

## WorkFlow

1. 先调用`interview-post-crawl` MCP server提供的fetch_interview_post工具(即...)来抓取帖子正文内容，并且该工具会返回应写入的文件路径和元数据等信息；
  

2. 如果分享链接来自小红书，且抓取失败原因是登录态缺失或过期，提示用户“小红书登录信息已失效，请重新登录”；

3. 如果帖子包含多次面试的面经，比如同时包含腾讯一面，阿里二面内容，那就分别生成2个md文件；

4. 生成 Markdown 面经笔记时，AI 简要回答部分要简洁精炼，无需长篇大论。

5. 调用系统工具写入相应markdown文件，要求：
   - 使用 Markdown 的一级标题作为 `filename`。
   - 将完整 Markdown 作为 `content` 传入。

## Markdown 格式

使用下面的结构。没有内容的可选部分可以省略。

```markdown
# 标题（公司名+部门+一面/二面..., 若没有这些信息，则用帖子的默认标题名(只保留正常文字如中文数字英文，表情包等忽略) ）
面试时间: (指的是面试时间，如果有说的话)
来源: 牛客/小红书 
url: ...
发布时间: ...（指的是帖子发布时间，没有就忽略）
抓取时间: ...
生成模型：... (爬取工具会告诉你)

题目如下：
## 项目相关
1. 问题1；
原文回答: xxx（如果有发帖人自己的回答的话，这部分不要压缩精炼，保留原文）
AI简要回答：xxx
2. 问题2：...

## 八股(比较宽泛，场景题等都可以包含在内)
1. 问题1；
原文回答: xxx（如果有发帖人自己的回答的话，这部分不要压缩精炼，保留原文）
AI简要回答：xxx
2. 问题2: ...

## 手撕
1. 两数之和；
AI简要思路：xxx

## (可选，其他分类)
...

## 面试者的感言(如果有的话，就写，原封不动就行，没有就可以省略该行)
```
