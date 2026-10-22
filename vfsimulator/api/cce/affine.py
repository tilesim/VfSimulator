"""Parse integer affine memory expressions into Canonical coefficients."""
import ast
from typing import Mapping

from vfsimulator.api.common.schema import AffineExpression, AffineTerm


def parse_affine_expression(
    value: int | str,
    *,
    params: Mapping[str, int],
    induction_variables: frozenset[str],
) -> AffineExpression:
    if isinstance(value, int) and not isinstance(value, bool):
        return AffineExpression(constant=value)
    try:
        tree = ast.parse(str(value).strip(), mode="eval")
    except SyntaxError as error:
        raise ValueError(f"Invalid affine offset expression: {value}") from error

    def add(
        lhs: tuple[int, dict[str, int]],
        rhs: tuple[int, dict[str, int]],
        scale: int = 1,
    ) -> tuple[int, dict[str, int]]:
        constant = lhs[0] + scale * rhs[0]
        terms = dict(lhs[1])
        for variable, coefficient in rhs[1].items():
            terms[variable] = terms.get(variable, 0) + scale * coefficient
            if terms[variable] == 0:
                del terms[variable]
        return constant, terms

    def evaluate(node: ast.AST) -> tuple[int, dict[str, int]]:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int) and not isinstance(node.value, bool):
                return int(node.value), {}
            raise ValueError(f"Non-integer affine offset constant: {value}")
        if isinstance(node, ast.Name):
            if node.id in params:
                return int(params[node.id]), {}
            if node.id in induction_variables:
                return 0, {node.id: 1}
            raise ValueError(f"Unknown affine offset symbol {node.id!r}: {value}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = evaluate(node.operand)
            return result if isinstance(node.op, ast.UAdd) else (-result[0], {k: -v for k, v in result[1].items()})
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            return add(evaluate(node.left), evaluate(node.right), -1 if isinstance(node.op, ast.Sub) else 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if left[1] and right[1]:
                raise ValueError(f"Non-affine variable multiplication: {value}")
            if left[1]:
                factor = right[0]
                return left[0] * factor, {key: coefficient * factor for key, coefficient in left[1].items()}
            factor = left[0]
            return right[0] * factor, {key: coefficient * factor for key, coefficient in right[1].items()}
        raise ValueError(f"Unsupported affine offset expression: {value}")

    constant, terms = evaluate(tree)
    return AffineExpression(
        constant=constant,
        terms=tuple(
            AffineTerm(variable_id=variable, coefficient=coefficient)
            for variable, coefficient in sorted(terms.items())
        ),
    )
