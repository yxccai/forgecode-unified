"""报告从实际结果汇总；未知用量保留为空。"""
# 【模块框架】读报告入口：把现有结果重新汇总，不重新做题。
# 连接位置：cli report → report.main → summarize
# 流过这里的数据：输入 results.jsonl，每个非空行一条实验记录。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import argparse
import json
from pathlib import Path
from forgecode.evaluation.runner import summarize

# 【函数框架 main】读取实际 JSONL 并输出汇总。
# 输入：results 文件参数。 输出：终端 JSON 报告。
# 1. 解析路径
# 2. 逐行读 JSON
# 3. 调用唯一 summarize，打印可读结果
def main(argv=None):
    # 小例子：report artifacts/real-evaluation/results.jsonl。
    # 留意：它不会补造缺失实验或把未知用量变成已知。
    parser = argparse.ArgumentParser()
    parser.add_argument("results", help="results.jsonl")
    args = parser.parse_args(argv)
    # 推导式把循环写紧凑：从 Path(args.results).read_text(encoding='utf-8').splitlines() 逐项
    # 取出 line，仅保留满足 line.strip() 的项，收集成列表。
    # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
    # read_text： 把文件按指定编码读成文字；结果是内容，不是文件路径。
    rows = [json.loads(line) for line in Path(args.results).read_text(encoding="utf-8").splitlines() if line.strip()]
    # dumps： 把对象编码成 JSON 文字；ensure_ascii=False 保留中文，得到的是文字而不是字典。
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))
