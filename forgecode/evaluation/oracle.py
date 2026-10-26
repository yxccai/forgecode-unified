"""复用原项目的有效实现，依赖已改为统一包；详见 docs/architecture.md。"""
# 【模块框架】独立阅卷室：在 Agent 工作区之外生成完整测试并核对结构要求。
# 连接位置：run_one/self_check → evaluate_files → test_source → command → 分数
# 流过这里的数据：Task 定义 cases 与 constraint；评分唯一入口 evaluate_files。
# 读法：先看函数上方的步骤，再顺着关键语句旁的中文注释追踪输入、结果与分支。
import ast
import json
from pathlib import Path
import tempfile

# 【函数框架 evaluate_files】独立运行完整验收，并检查指定代码结构。
# 输入：Task 与文件名→源码字典。 输出：passed/total/structural/success/输出。
# 1. 在新临时目录安全写文件
# 2. 生成完整验收测试与 runner
# 3. 限时执行并解析测试计数
# 4. AST 检查 no_loops/join 等约束
# 5. 用测试数量、通过数和结构共同决定 success
def evaluate_files(task, files):
    """验收数据在 Agent 工作区外；同时支持单文件和跨文件任务。"""
    # 小例子：公开用例通过但边界用例失败 → independent_acceptance 失败。
    # 留意：工作区分离不是对恶意原生代码的系统沙箱；评分执行候选源码，不能说可抵抗所有攻击。
    # 把实现交到工作区外：临时目录写候选文件；绝对/.. 名称拒绝。隔离目录不是系统级恶意代码沙箱。
    from pathlib import PurePath
    # with 限定资源的使用范围；离开缩进块时执行退出逻辑，异常路径也会收尾。
    with tempfile.TemporaryDirectory(prefix="forgecode-oracle-") as root:
        for name, text in files.items():
            raw = PurePath(name)
            if raw.is_absolute() or ".." in raw.parts:
                # raise 在这里终止正常路径，把原因交给外层匹配的 except；后面的正常操作不会继续。
                raise ValueError("Invalid oracle source path")
            target = Path(root) / raw
            # mkdir： 创建目录；exist_ok 允许已经存在，parents 允许补父目录，具体行为看这里传入的参数。
            target.parent.mkdir(parents=True, exist_ok=True)
            # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
            target.write_text(text, encoding="utf-8")
        # 完整验收用例在这里生成：test_source 使用题目全部 cases，runner 收集实际 tests/failures/
        # errors。
        Path(root, "test_acceptance.py").write_text(test_source(task), encoding="utf-8")
        runner = "import json, unittest\nsuite=unittest.defaultTestLoader.discover('.', pattern='test_acceptance.py')\nresult=unittest.TestResult()\nsuite.run(result)\nprint(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors)}))\n"
        # write_text： 到这里才把文字写入文件；encoding="utf-8" 明确中文的保存方式。
        Path(root, "oracle.py").write_text(runner, encoding="utf-8")
        import sys
        # 限时运行并解析真实计数：非零退出或坏 JSON 不能误判通过；至少要核对实际测试总数。
        result = command(root, [sys.executable, "oracle.py"], timeout=10)
        try:
            # 条件表达式：条件 result['exit_code'] == 0 成立取前面的值，否则取 else 后面的值。
            # loads： 把 JSON 文字解析成对象；格式不合法会抛异常，解析后还要检查结构。
            scores = json.loads(result["output"].splitlines()[-1]) if result["exit_code"] == 0 else {}
        except (ValueError, IndexError):
            scores = {}
        total = len(task.cases)
        passed = max(0, scores.get("tests", 0)-scores.get("failures", 0)-scores.get("errors", 0))
        # 行为正确还可能违反结构要求：AST 检查 no_loops/join；no_mutation 已写入测试。语法坏或目标
        # 缺失时 structural=False。
        structural = True
        try:
            tree = ast.parse(files["subject.py"])
            if task.constraint in {"no_loops", "join"}:
                # 推导式把循环写紧凑：从 ast.walk(tree) 逐项取出 n，收集成迭代器。
                structural = not any(isinstance(n, (ast.For, ast.While, ast.AsyncFor)) for n in ast.walk(tree))
            if task.constraint == "join":
                # 推导式把循环写紧凑：从 ast.walk(tree) 逐项取出 n，收集成迭代器。
                structural = structural and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "join" for n in ast.walk(tree))
        except (SyntaxError, KeyError):
            structural = False
        # 多条件共同决定 success：passed==total、实际运行用例==total、结构符合才通过，不能只看一句
        #  OK。
        # 切片只取指定范围：左边界包含、右边界不包含；负数从末尾计数，省略边界表示取到头或尾。
        return {"passed": passed, "total": total, "structural": structural,
                "success": passed == total and scores.get("tests") == total and structural,
                "output": result["output"][:2000]}

from forgecode.execution.process import command

# 【函数框架 function_name】从题目参考源码找要验收的顶层函数名。
# 输入：source。 输出：第一个顶层 FunctionDef 的 name。
# 1. ast.parse 解析文本
# 2. 只检查顶层 body
# 3. next 取第一个函数
def function_name(source):
    # 小例子：'def total(values):...' → 'total'。
    # 留意：AST 解析不执行源码；题目定义必须确有顶层目标函数。
    # next： 取迭代器的下一项；耗尽时抛 StopIteration，或者使用这里明确提供的默认值。
    # 推导式把循环写紧凑：从 ast.parse(source).body 逐项取出 node，仅保留满足 isinstance(node,
    #  ast.FunctionDef) 的项，收集成迭代器。
    return next((node.name for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)))

# 【函数框架 test_source】把用例数据变成 unittest 测试源码。
# 输入：Task，或只选部分 cases。 输出：测试 Python 文本。
# 1. 导入目标函数并生成测试类
# 2. 每项造输入并 deepcopy
# 3. 普通期望 assertEqual，异常期望 assertRaises
# 4. no_mutation 额外比较输入前后
def test_source(task, cases=None):
    # 小例子：median 的 no_mutation 要求返回正确且不改原列表。
    # 留意：测试数据 repr 写进代码；题目定义是受信数据，不能把此生成器用于未校验的任意代码插入。
    # 条件表达式：条件 cases is None 成立取前面的值，否则取 else 后面的值。
    # is None 检查有没有值，is not None 检查有值；0、空列表与空文字都不等于 None。
    selected = task.cases if cases is None else cases
    lines = ['import unittest, copy', f'from subject import {function_name(task.solution)} as function', 'class Acceptance(unittest.TestCase):']
    for index, (argument, expected) in enumerate(selected):
        lines += [f'    def test_{index}(self):', f'        value = {argument!r}', '        before = copy.deepcopy(value)']
        if isinstance(expected, dict) and 'raises' in expected:
            lines += [f'        with self.assertRaises({expected['raises']}):', '            function(value)']
        else:
            lines += [f'        self.assertEqual(function(value), {expected!r})']
        if task.constraint == 'no_mutation':
            lines += ['        self.assertEqual(value, before)']
    return '\n'.join(lines) + '\n'

# 【函数框架 evaluate_source】为旧单文件调用方式提供同一个评分入口。
# 输入：task、subject.py source。 输出：evaluate_files 的结果。
# 1. 把源码命名为 subject.py
# 2. 补任务原始 extra_files
# 3. 委托 evaluate_files
def evaluate_source(task, source):
    """单文件兼容入口；评分只有 evaluate_files 一条路径。"""
    # 小例子：单文件 sum 评测仍只走唯一阅卷路径。
    # 留意：跨文件修复要用 evaluate_files 传最终所有文件，不能只改 subject.py。
    # 字典里的 ** 把 dict(task.extra_files) 的字段展开进来；后面的同名字段覆盖前面的字段。
    return evaluate_files(task, {"subject.py": source, **dict(task.extra_files)})
