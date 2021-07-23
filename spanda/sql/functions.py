import math
import warnings
import pandas as pd
from spanda.core.typing import *
from spanda.core.utils import wrap_col_args

# helper functions
sum_ = sum
abs_ = abs


def _elementwise_apply(f: Callable):
    def f_elementwise(x: pd.Series) -> pd.Series:
        return x.apply(f)
    return f_elementwise


# classes
class Column:
    """
    A column in Spanda dataframe
    """

    def __init__(self, name: Optional[str]):
        self.name = name
        self.op = lambda df: df[name]

    def setOp(self, op: Callable):
        self.op = op

    @staticmethod
    def getName(col: Union['Column', str]) -> str:
        if isinstance(col, Column):
            return col.name
        else:
            return str(col)

    @staticmethod
    def _apply(col: 'Column', df: pd.DataFrame) -> Union[pd.Series, Any]:
        if isinstance(col, Column):
            return col.op(df)
        else:
            return col

    @staticmethod
    def _transformColumn(name: str, operation: Callable) -> 'Column':
        col = Column(None)
        col.name = name
        col.op = operation
        return col

    def _simpleBinaryTransformColumn(self, opname: str, opfunc: Callable, other: Union['Column', Any],
                                     is_other_col: bool = True) -> 'Column':
        return Column._transformColumn(f"({Column.getName(self)} {opname} {Column.getName(other)})",
                                       lambda df: opfunc(Column._apply(self, df),
                                                         Column._apply(other, df) if is_other_col else other))

    def _simpleUnaryTransformColumn(self, opname: str, opfunc: Callable) -> 'Column':
        return Column._transformColumn(f"({opname}{Column.getName(self)})", lambda df: opfunc(Column._apply(self, df)))

    def __repr__(self):
        return f"<Column {self.name}>"

    def alias(self, name):
        """
        Return column with new name
        """

        return Column._transformColumn(name, self.op)

    def between(self, start, end):
        """
         A boolean expression that is evaluated to true if the value of this
         expression is between the given columns.

         >>> df.select('name', F.col('age').between(2, 4))
         +-----+---------------------------+
         | name|((age >= 2) AND (age <= 4))|
         +-----+---------------------------+
         |Alice|                       true|
         |  Bob|                      false|
         +-----+---------------------------+
         """

        return (self >= start) & (self <= end)

    def apply(self, func: Callable) -> 'Column':
        return udf(func)(self)

    def isin(self, values):
        """
        A boolean expression that is evaluated to true if the value of this
        expression is contained by the evaluated values of the arguments.
        """
        return self._simpleBinaryTransformColumn('IN', lambda x, y: x.isin(y), values, is_other_col=False)

    # operators
    def __eq__(self, other):
        return self._simpleBinaryTransformColumn('==', lambda x, y: x == y, other)

    def __ne__(self, other):
        return self._simpleBinaryTransformColumn('!=', lambda x, y: x != y, other)

    def __gt__(self, other):
        return self._simpleBinaryTransformColumn('>', lambda x, y: x > y, other)

    def __lt__(self, other):
        return self._simpleBinaryTransformColumn('<', lambda x, y: x < y, other)

    def __ge__(self, other):
        return self._simpleBinaryTransformColumn('>=', lambda x, y: x >= y, other)

    def __le__(self, other):
        return self._simpleBinaryTransformColumn('<=', lambda x, y: x <= y, other)

    def __add__(self, other):
        return self._simpleBinaryTransformColumn('+', lambda x, y: x + y, other)

    def __mul__(self, other):
        return self._simpleBinaryTransformColumn('*', lambda x, y: x * y, other)

    def __div__(self, other):
        return self._simpleBinaryTransformColumn('/', lambda x, y: x / y, other)

    def __sub__(self, other):
        return self._simpleBinaryTransformColumn('-', lambda x, y: x - y, other)

    def __and__(self, other):
        return self._simpleBinaryTransformColumn(' AND ', lambda x, y: x & y, other)

    def __or__(self, other):
        return self._simpleBinaryTransformColumn(' OR ', lambda x, y: x | y, other)

    def __neg__(self, other):
        return self._simpleUnaryTransformColumn('-', lambda x: -x)

    def __invert__(self):
        return self._simpleUnaryTransformColumn('NOT ', lambda x: ~x)


class AggColumn:
    def __init__(self, name, orig_col, op):
        self._name = name
        self._orig_col = orig_col
        self._op = op

    def alias(self, name):
        # TODO: might want to clone orig_col and op (maybe also in Column)
        return AggColumn(name=name, orig_col=self._orig_col, op=self._op)

    @staticmethod
    def getName(agg_col):
        return f"{agg_col._name} ({Column.getName(agg_col._orig_col)})"

    @staticmethod
    def _apply(agg_col: 'AggColumn', df: pd.DataFrame) -> pd.Series:
        return agg_col._op(Column._apply(agg_col._orig_col, df))

    def over(self, window_spec) -> Column:
        def f(df):
            inputs = Column._apply(self._orig_col, df)
            # in the following, row2grp represents the representative group of each row, while grp2rows is a dictionary
            # of all rows that the group aggregates over. possibly not intuitive but group may include rows that are
            # not represented by this group (for example if we apply lead(...) we aggregate over the next row which is
            # not represented by the current group)
            row2grp, grp2rows = window_spec._get_group_data(df)
            grp_agg_val = {grp: self._op(inputs.loc[vals]) for grp, vals in grp2rows.items()}
            data = {row_idx: grp_agg_val[grp_name] for row_idx, grp_name in row2grp.items()}
            return pd.Series(data=data, index=inputs.index)

        col = Column(name=f"{AggColumn.getName(self)} OVER ({window_spec._name})")
        col.setOp(f)
        return col


class WindowTransformationColumn(AggColumn):
    def __init(self, name: str, orig_col: Column, op: Callable):
        self._name = name
        self._orig_col = orig_col
        self._op = op

    @staticmethod
    def _apply(agg_col, df: pd.DataFrame):
        assert False, f"cannot aggregate grouped data using `{agg_col._name}`. can be used only over windows."

    def over(self, window_spec) -> Column:
        def f(df):
            orig_col = self._orig_col
            if orig_col is None:
                orig_col = window_spec._get_orderby_col()
            inputs = Column._apply(orig_col, df)
            row2grp, grp2rows = window_spec._get_group_data(df)
            data = {row_idx: self._op(inputs, grp2rows[grp_name], grp2rows[grp_name].index(row_idx))
                    for row_idx, grp_name in row2grp.items()}
            return pd.Series(data=data, index=inputs.index)

        col = Column(name=f"{AggColumn.getName(self)} OVER ({window_spec._name})")
        col.setOp(f)
        return col


# functions
def col(name: str) -> Column:
    """
    Creates a column object with this name
    """
    if name == "*":
        return Column._transformColumn("*", lambda df: df.apply(lambda x: tuple(x), axis='columns'))
    return Column(name)


def lit(value: Any):
    """
    Returns a column representing the literal value it received as value
    """
    return Column._transformColumn(f"LIT( {str(value)} )", lambda df: value)


# column functions
@wrap_col_args
def sqrt(col: Column) -> Column:
    """
    Computes square root
    """
    return col._simpleUnaryTransformColumn("SQRT ", _elementwise_apply(math.sqrt))


@wrap_col_args
def exp(col: Column) -> Column:
    """
    Computes exp function
    """
    return col._simpleUnaryTransformColumn("EXP ", _elementwise_apply(math.exp))


@wrap_col_args
def log(col: Column) -> Column:
    """
    Computes logarithm function
    """
    return col._simpleUnaryTransformColumn("LOG ", _elementwise_apply(math.log))


@wrap_col_args
def _abs(col: Column) -> Column:
    """
    Computes absolute value
    """
    return col._simpleUnaryTransformColumn("ABS ", _elementwise_apply(abs_))


@wrap_col_args
def array(*cols: Column) -> Column:
    """
    Return column of arrays
    """
    return (struct(*cols).apply(list)).alias(f"[{', '.join([Column.getName(c) for c in cols])}]")


def array_contains(col: Column, value: Any) -> Column:
    """
    Return whether value is contained in the array
    """
    return col._simpleUnaryTransformColumn(f"{Column.getName(lit(value))} CONTAINED IN ",
                                           _elementwise_apply(lambda x: value in x))


@wrap_col_args
def array_distinct(col: Column) -> Column:
    """
    Return for every entry in the column (of arrays), remove duplicates
    """
    return col._simpleUnaryTransformColumn(f"ARRAY_DISTINCT ", _elementwise_apply(lambda x: list(set(x))))


@wrap_col_args
def corr(col1: Column, col2: Column) -> Column:
    """
    Compute the Pearson correlation between col1 and col2
    """
    return col1._simpleBinaryTransformColumn("CORR WITH", lambda x, y: x.corr(y, method='pearson'), col2)


def concat_ws(sep: str, *cols: Column) -> Column:
    """
    Concatenate string cols with sep as the separator
    """
    return (struct(*cols).apply(sep.join)
            .alias(f'CONCAT_WS("{str(sep)}", {", ".join([Column.getName(c) for c in cols])})'))


@wrap_col_args
def cos(col: Column) -> Column:
    """
    Computes cosine function
    """
    return col._simpleUnaryTransformColumn("COS ", _elementwise_apply(math.cos))


@wrap_col_args
def sin(col: Column) -> Column:
    """
    Computes sine function
    """
    return col._simpleUnaryTransformColumn("SIN ", _elementwise_apply(math.sin))


@wrap_col_args
def tan(col: Column) -> Column:
    """
    Computes tangent function
    """
    return col._simpleUnaryTransformColumn("TAN ", _elementwise_apply(math.tan))


@wrap_col_args
def struct(*cols: Column) -> Column:
    """
    Takes in columns and returns a single struct column
    """
    return udf(lambda *x: tuple(x))(*cols).alias(f"({', '.join([Column.getName(col) for col in cols])})")


def udf(func: Callable) -> Callable:
    """
    Transforms function `func` into a function that applies `func` elementwise on a column
    """

    @wrap_col_args
    def f(*cols: Column) -> Column:
        return Column._transformColumn(f"UDF `{func.__name__}` ({', '.join([Column.getName(col) for col in cols])})",
                                       lambda df: pd.concat([Column._apply(col, df) for col in cols],
                                                            axis='columns').apply(lambda c: func(*c), axis='columns'))
    return f


# aggregate functions
@wrap_col_args
def _min(col: Column) -> AggColumn:
    """
    Aggregate function: compute minimum
    """
    return AggColumn(name="MIN", orig_col=col, op=lambda x: x.min())


@wrap_col_args
def _max(col: Column) -> AggColumn:
    """
    Aggregate function: compute maximum
    """
    return AggColumn(name="MAX", orig_col=col, op=lambda x: x.max())


@wrap_col_args
def mean(col: Column) -> AggColumn:
    """
    Aggregate function: compute mean
    """
    return AggColumn(name="MEAN", orig_col=col, op=lambda x: x.mean())


@wrap_col_args
def first(col: Column) -> AggColumn:
    """
    Aggregate function: take first entry in the group
    """
    return AggColumn(name="FIRST", orig_col=col, op=lambda x: x.iloc[0])


@wrap_col_args
def last(col: Column) -> AggColumn:
    """
    Aggregate function: take last entry in the group
    """
    return AggColumn(name="LAST", orig_col=col, op=lambda x: x.iloc[-1])


def count(col: Column) -> AggColumn:
    """
    Aggregate function: count the number of elements in the group.
    NOTE: Not affected by the value of `col`!!
    """
    if col != "*":
        warnings.warn("count(col) ignores the column it gets. Use count('*') instead to avoid this message")
    return AggColumn(name="COUNT", orig_col=col, op=len)


@wrap_col_args
def countDistinct(col: Column) -> AggColumn:
    """
    Aggregate function: count the number of distinct elements in `col` for each group
    """
    # TODO: this functions needs to be fixed
    return AggColumn(name="COUNT DISTINCT", orig_col=col, op=lambda x: len(set(x[Column.getName(col)])))


@wrap_col_args
def collect_list(col: Column) -> AggColumn:
    """
    Aggregate function: collect all elements in group into a list
    """
    return AggColumn(name="COLLECT_LIST", orig_col=col, op=list)


@wrap_col_args
def collect_set(col: Column) -> AggColumn:
    """
    Aggregate function: collect all elements in group into a set
    """
    return AggColumn(name="COLLECT_SET", orig_col=col, op=set)


@wrap_col_args
def _sum(col: Column) -> AggColumn:
    """
    Aggregate function: compute sum
    """
    return AggColumn(name="SUM", orig_col=col, op=lambda x: x.sum())


@wrap_col_args
def sumDistinct(col: Column) -> AggColumn:
    """
    Aggregate function: sum distinct values
    """
    return AggColumn(name="SUM DISTINCT", orig_col=col, op=lambda x: sum_(set(x)))


# window-only functions
@wrap_col_args
def lead(col: Column, count: int = 1) -> WindowTransformationColumn:
    """
    Window function: lead function
    """
    return WindowTransformationColumn(name=f"LEAD {count}", orig_col=col,
                                      op=lambda col, grp, pos: col.loc[grp[pos+count]] if pos+count < len(grp) else None)


@wrap_col_args
def lag(col: Column, count: int = 1) -> WindowTransformationColumn:
    """
    Window function: lag function
    """
    return WindowTransformationColumn(name=f"LAG {count}", orig_col=col,
                                      op=lambda col, grp, pos: col.loc[grp[pos-count]] if pos-count >= 0 else None)


def dense_rank() -> WindowTransformationColumn:
    """
    Window function: dense rank function
    """
    return WindowTransformationColumn(name=f"DENSE RANK", orig_col=None,
                                      op=lambda col, grp, pos: pos)


def rank() -> WindowTransformationColumn:
    """
    Window function: rank function
    """
    # TODO: check
    return WindowTransformationColumn(name=f"RANK", orig_col=None,
                                      op=lambda col, grp, pos: list(col.loc[grp]).index(col[grp[pos]]))


min = _min
max = _max
sum = _sum
abs = _abs
