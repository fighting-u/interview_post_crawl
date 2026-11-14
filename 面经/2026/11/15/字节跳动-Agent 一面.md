# 字节跳动-Agent 开发一面
面试时间: 
来源: 牛客 
url: https://www.nowcoder.com/share/jump/1221189251185911342
发布时间: 2026-10-15T16:46:18+08:00
抓取时间: 2026-11-15T00:10:21+08:00
生成模型：qwen3.7-flash

题目如下：
## 项目相关
1. 讲一讲你的实习项目，重点讲项目中遇到的难点以及怎么解决这些难点？
   AI 简要回答：建议使用 STAR 原则。重点阐述技术选型背后的 **Trade-off（权衡）**，例如为什么选 A 不选 B；遇到的具体瓶颈（如高并发下的响应延迟、资源限制）；以及如何通过优化算法、引入缓存或异步化手段来解决它。

2. 所以你的恢复是 graph 的恢复，而不是 agent 的 session 的恢复，是吗？
   AI 简要回答：需要明确概念边界。Graph 通常指执行流程（工作流状态），Session 指 Agent 的记忆/上下文。面试者需确认两者在系统中的定义差异。

3. 那你可以讲讲你在做中断恢复的这个过程中，你做了哪些事情来保持记忆能够连续？
   AI 简要回答：通常涉及持久化层的设计（如 DB/MQ 存储节点状态）和内存层的映射。确保状态保存时捕获了关键变量、上下文窗口（Context Window）以及工具调用的返回值。

4. 那这个 graph 是你们自己实现的一个 graph 是吗？
   AI 简要回答：如实回答自研还是基于 LangGraph/Dagster 等框架二次开发。如果是自研，简述 DSL 设计思路。

5. 所以你的 graph 其实是一个代码逻辑，对不对？它里面 graph 的中间节点会有 Agent 的调用，对吧？
   AI 简要回答：Graph 本质是有向图。节点是任务（Task），边是流转条件。Agent 是其中一种执行 Node 类型（LLM Call）。

6. 就是中断恢复其实都是代码逻辑，不会影响 Agent 的，是吗？
   AI 简要回答：底层确实主要是状态机的序列化/反序列化逻辑。但要指出：如果 Agent 依赖的向量库或数据库在中断期间发生了变更（如并发写入），则会影响结果。

7. 假设现在整个流程是一个 Agent 的 skill 去执行这段流程，流程需要等待用户，用户可能一个小时或者一天都不去操作，那你觉得这个 agent 它应该怎么来设计？
   AI 简要回答：**异步回调机制**。将当前状态保存到持久化存储，释放计算资源（线程/GPU）；当用户反馈到达时，通过 ID 唤醒进程并加载现场继续执行。避免长时间阻塞线程。

## Agent 架构与 Skill 设计
8. 你有没有了解过字节或者一些开源的 agent，它们对于中断和恢复这种是怎么做的？中断之后对记忆会做哪些操作？
   AI 简要回答：主流做法是 Checkpointing（检查点保存）。OpenClaw 等倾向于将对话历史完整快照；有些框架使用 Delta Log（增量日志）。记忆管理上，会保留最近 N 轮对话，丢弃无关早期上下文以节省 Token。

9. 那你有没有看过 Open Clow、Deepseek Harness 这类开源框架的实现？
   AI 简要回答：考察候选人对前沿框架的关注度。Harnes 侧重视觉与代码的混合理解，OpenClaw 侧重隐私与本地化部署。

10. 不管是 agent 还是 coding agent，调用大模型会产生幻觉，你了解过的 agent，是怎么去控制幻觉、防止幻觉？
    AI 简要回答：
    *   **Prompt 工程**：Few-shot examples，明确的 System Prompt。
    *   **Grounding（落地/溯源）**：强制 RAG，让模型必须参考检索到的文档回答。
    *   **Self-Correction**：让 LLM 自查代码或逻辑是否正确。

11. 不是讲具体实现，从 agent 设计角度，防幻觉有哪些方法？
    AI 简要回答：
    *   **结构化输出**：限制 LLM 只能输出 JSON，不可自由发挥。
    *   **确定性组件解耦**：能写代码解决的绝不给 LLM 猜。
    *   **人机协作 (Human-in-the-loop)**：关键步骤增加确认环节。

12. 幻觉也会出现在工具调用，tool call 调用错了，这种应该怎么处理？
    AI 简要回答：**重试机制 + Schema 校验**。LLM 输入参数错误时，将报错信息回传给 LLM 让其修正（ReAct 模式）。同时在代码层做严格的 DTO 校验，拦截非法参数。

13. 假设你要去实现一个 prompt 或者实现一个 skill，那如何在这个 skill 中去防幻觉呢？
    AI 简要回答：Skill 封装中内置了 **Guardrails（护栏）**。例如：在执行代码前先运行单元测试；在生成文案前限制风格约束。

14. 你写过 skill 吗？讲一下生产一个 skill 的完整流程。
    AI 简要回答：需求分析 -> API/能力定义 (Doc) -> Prompt/脚本编写 -> 沙盒测试 -> 版本化管理 (Version Control)。

15. 业务逻辑确定之后，开发 skill 的过程是什么？做领域设计、产出 Spec 这些会做吗？
    AI 简要回答：产出 Input/Output Schema，明确边界条件（Corner Cases），进行异常处理设计（Timeout, Retry）。

16. skill 写出来之后，如何评估它满足上线质量？
    AI 简要回答：自动化评测集（Unit Test, Golden Datasets）和人工抽检。关注准确率、响应时间和成本。

17. skill 上线之后，如何去做它的效果监控？
    AI 简要回答：埋点收集调用成功率、用户满意度反馈、Token 消耗分布、延迟 P99。

18. 现在你有了用户的反馈，你如何对这个 skill 去做进化？
    AI 简要回答：Bad Case 归因分析。是 Prompt 不好？还是知识源缺失？针对性地加入 Few-shot 例子或扩充 Vector Store。

19. 如果要维护非常多 skill，做不到人工优化，那怎么自动化做进化？
    AI 简要回答：利用 **Feedback Loop**，让评价 Agent (Evaluator) 自动打分；基于 Bad Case 自动生成新的 Prompt 变体进行对比测试（A/B Testing for Prompts）。

20. 有没有了解过进化相关的框架、算法或者论文？
    AI 简要回答：可提及 Evolutionary Algorithm 用于 Prompt 搜索，或 RLHF（人类反馈强化学习）。

21. 如果让你来做一个进化的 Agent，你会怎么做？
    AI 简要回答：构建 Plan-Do-Check 循环。生成新版本 -> 跑自动化测试集 -> 评估分数 -> 只有分数提升才合并主干。

## 多 Agent 协同
22. 你的 agent 会有 A-to-A 之间的调用吗？
    AI 简要回答：是的，Master-Slave（主从）结构最常见。

23. 你了解过多 Agent 协同下有哪些范式或者结构吗？
    AI 简要回答：
    *   **Hierarchical (层级式)**：Manager + Workers。
    *   **Swarm (群体式)**：无固定中心，互相通信。
    *   **Sequential (流水线)**：上游输出作为下游输入。

24. Sub-Agent 这种模式适合什么样的场景去使用？
    AI 简要回答：复杂任务拆解，如编程场景中分别由“代码生成 Agent"、“测试 Agent"、“审查 Agent"分工。

25. 主 Agent 给子 Agent 派任务，以 coding 场景举例，怎么保证给到子 Agent 的输入能够完成任务，同时不会传入过多 context，中间数据传输怎么设计？
    AI 简要回答：
    *   **上下文裁剪**：只传递相关文件和函数签名。
    *   **标准化接口**：强类型的 Request Payload。
    *   **共享状态**：通过全局 Context 对象而非直接传参传递公共资源。

26. 讲下你的 AI coding 的流程，举个新需求的例子，整个 loop 是怎么跑起来的？
    AI 简要回答：用户提需求 -> Agent 拆解 Task List -> 逐一生成代码 -> 提交 PR -> 自动 Run 测试用例 -> 失败则 Self-Debug 修复 -> 最终通知用户。

27. 项目中 harness 是怎么做的？有哪些目录分别做哪些事情？项目里对 Agent 的约束会记录在哪里？
    AI 简要回答：Harness 通常是调度层。记录了 Agent 的行为规范（Code of Conduct），如禁止访问敏感目录、必须带注释等，通常通过 System Prompt 或配置文件下发。

28. 这些知识会沉淀到一些地方吗？
    AI 简要回答：知识库（RAG Vector Store）或 Prompt 模板仓库。

29. 做一个需求的时候，你会设计多少个 agent，多少个 skill？
    AI 简要回答：宁缺毋滥。一般拆分为 Planner, Coder, Tester 三个核心 Agent，配合 5-10 个常用 Skill。

## 分布式系统与中间件
30. 假设下单场景，订单系统、商家库存系统是两套分布式系统；下单生成订单和扣减库存，两件事要保持一致，要么同时成功，要么同时失败，你怎么设计？
    AI 简要回答：
    *   **TCC (Try-Confirm-Cancel)**。
    *   **消息队列事务 (Transactional Outbox Pattern)**：先写本地库，发 MQ，消费者扣库存。

31. 那你觉得这个分布式事务应该怎么来设计？
    AI 简要回答：根据一致性要求选择。强一致用 Paxos/Raft (Seata AT/Saga)，最终一致性用 Local Message Table / RocketMQ Transactional Messages。

32. 所以是通过事务回滚的方式？那就要求系统要支持回滚操作，是吗？
    AI 简要回答：是的，这需要业务逻辑具备幂等性和逆向操作的能力（补偿机制）。

33. 那回到缓存和数据库，这两者怎么保持一致性？是全量加载到缓存，异步同步 DB 吗？
    AI 简要回答：推荐 **Cache Aside Pattern**。先更 DB，再删缓存（由后续请求回填）。严禁异步同步 DB，因为可能有脏数据竞争。

34. 分布式场景下 Redis 分布式锁是怎么实现的，了解过吗？（提到 Redlock）
    AI 简要回答：单机用 Lua 脚本 + SETNX。集群环境下考虑 Redlock 算法（获取多数节点的锁）以应对单节点宕机，但争议较大，通常业务允许少量丢失时使用 Redisson 方案。

35. Redis 你用过哪些数据结构？
    AI 简要回答：String, Hash, List, Set, ZSet, Bitmap, Bloom Filter, Stream.

36. Zset 它底层的数据结构是怎样的？
    AI 简要回答：跳跃表 (SkipList) + HashTable。HashTable 用于 O(1) 查值，SkipList 用于 O(logN) 排序遍历。

37. 跳表的具体结构是怎样的？假如向跳表里新增一个数据，它会怎么写入？
    AI 简要回答：分层链表。插入时随机生成层数，逐层找到合适位置指针插入。查找走高位跳过无效节点。

38. 使用 MQ 做一致性，MQ 可以做到不丢消息吗？
    AI 简要回答：
    *   **生产者**：同步发送 + Ack 确认 + 本地重试表。
    *   **Broker**：多副本同步刷盘 (Sync Flush)。
    *   **消费者**：手动 Ack (Auto-ack off)。

39. MQ 场景：一个 consumer group 有多台机器消费同一个 topic，但只有一台机器在消费，尽可能说出所有可能的原因？
    AI 简要回答：
    *   Topic Partition 数量 = 正在消费的机器数。
    *   其他机器虽然在线，但分配不到 Partition（负载不均）。
    *   其他机器代码报错导致 Rebalance 频繁或退出。

## Java 基础与并发
40. 你的主要开发语言是 Java 对吧？
    AI 简要回答：确认。

41. ConcurrentHashMap 它是怎么实现线程安全的？
    AI 简要回答：
    *   JDK 1.7：Segment 数组 + HashEntry 数组 + 锁分离 (ReentrantLock)。
    *   JDK 1.8：Node 数组 + 链/红黑树 + synchronized 锁头节点 + CAS 无锁插入。

42. ConcurrentHashMap 怎么解决哈希冲突？
    AI 简要回答：链表法（开放寻址的变种，这里指拉链法）。桶内元素达到阈值转红黑树。

43. ConcurrentHashMap 里面 CAS 的比对然后覆盖的逻辑是什么样？
    AI 简要回答：Compare-And-Swap。读取旧值，计算新值，若内存中旧值未变则原子替换；否则自旋重试。

44. CAS 会产生 ABA 问题，怎么解决 ABA 问题？
    AI 简要回答：
    *   **AtomicStampedReference**：引入版本号戳。每次修改版本号 +1。
    *   **AtomicMarkableReference**：布尔标记位。

## 面试者的感言
45. 你还有笔试，是已经约好笔试时间了吗？
    AI 简要回答：跟进求职流程进度。

## 面试者的感言
该岗位侧重于 Agent 编排、LangChain/LangGraph 原理以及扎实的后端基本功（分布式、Redis、并发）。