import abc
import copy
import inspect
import keyword
from contextvars import ContextVar
from functools import cached_property
from typing import Callable, Iterable, List, Protocol, Tuple, Type, TypeVar, Union

from .escape import escape, safe
from .utils import _is_iterable

# previous frame id (frame.f_back), parent object
last_parent = ContextVar("last_parent", default=(None, None))

T = TypeVar("T")
CompSelf = TypeVar("CompSelf", bound="_ComponentBase")
ChildSelf = TypeVar("ChildSelf", bound="_ChildrenMixin")
StrType = Union[str, safe]
ContentType = Union[StrType, CompSelf]


class ComponentClass(Protocol):
    def render(self, children: safe) -> Union[ContentType, Iterable[ContentType]]:
        ...


class _ComponentBase:
    _argnames: list
    _defaults = None
    props: dict

    def __init__(self, *args, **kwargs):
        props = copy.deepcopy(self._defaults) if self._defaults else {}

        if args:
            for argname, argval in zip(self._argnames, args):
                props[argname] = argval

        props.update(kwargs)
        self.props = props

    def replace(self, **kwargs) -> CompSelf:
        return self._merge(kwargs)

    def append(self, **kwargs) -> CompSelf:
        appended = {}
        for key, val in kwargs.items():
            if key in self.props:
                val = self.props[key] + val
            appended[key] = val
        return self._merge(appended)

    def merge(self, **kwargs) -> CompSelf:
        if overlapping := [key for key in kwargs if key in self.props]:
            overlapping_keys = ", ".join(overlapping)
            raise KeyError(
                f"{overlapping_keys} already specified in {self!r}, "
                "use the replace method if you want to replace arguments"
            )
        return self._merge(kwargs)

    def copy(self) -> CompSelf:
        return self._merge({})

    def __repr__(self):
        proplist = ", ".join(f"{k}={v!r}" for k, v in self.props.items())
        return f"<{self.__class__.__name__}({proplist})>"

    def __mul__(self, other: int) -> CompSelf:
        if not isinstance(other, int):
            return NotImplemented

        # Every component is safe by default, so the result should be safe too
        Multiple = Component(lambda: safe(self) * other)
        Multiple.__name__ = "Multi" + self.__class__.__name__
        Multiple.__doc__ = "Multiple: " + (self.__doc__ or "")
        return Multiple()

    def _merge(self, kwargs) -> CompSelf:
        merged_props = {**copy.deepcopy(self.props), **kwargs}
        return self.__class__(**merged_props)


class _ChildrenMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._children = []

    def __enter__(self) -> ChildSelf:
        parent_frame_id, parent = last_parent.get()
        self._parent_frame_id = parent_frame_id
        self._parent = parent
        current_frame_id = id(inspect.currentframe().f_back)
        last_parent.set((current_frame_id, self))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        current_frame_id = id(inspect.currentframe().f_back)
        if self._parent is not None and current_frame_id == self._parent_frame_id:
            self._parent._children.append(self)
        last_parent.set((self._parent_frame_id, self._parent))

    def __iadd__(self, other) -> ChildSelf:
        self._children.append(other)
        return self

    def __class_getitem__(cls, key) -> ChildSelf:
        return cls()[key]

    def __getitem__(self, children) -> safe:
        if _is_iterable(children):
            # str is a special case, because it's an iterator too
            # _ChildrenMixins are also iterators because of this very method
            if isinstance(children, (str, safe, _ChildrenMixin)):
                children = (children,)
        else:
            children = (children,)

        return self.__render(children)

    def _escape(self, children) -> List[safe]:
        escaped_children = []
        for ch in children:
            is_component = isinstance(ch, _ComponentBase)
            safe_ch = safe(ch) if is_component else escape(ch)
            escaped_children.append(safe_ch)
        return escaped_children

    def __str__(self) -> safe:
        return self.__render(self._children)

    def __render(self, children: safe) -> safe:
        if not children:
            return self._render(safe(""))
        escaped_children = self._escape(children)
        safe_children = safe("".join(escaped_children))
        return self._render(safe_children)


class _LazyComponent(_ChildrenMixin, _ComponentBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lazy = False

    @property
    def lazy(self) -> ChildSelf:
        new = self.copy()
        new._lazy = True
        return new

    def __getitem__(self, children) -> safe:
        if not self._lazy:
            return super().__getitem__(children)

        if self._children:
            # Not AttributeError because it would be confusing, for ex. when using hasattr.
            # Also this is like a function call, so a ValueError makes sense
            raise ValueError("Lazy component already has children")

        self._children = children
        return self


class _ContentMixin(metaclass=abc.ABCMeta):
    def _render(self, children: safe) -> safe:
        content = self._get_content(children)

        if isinstance(content, safe):
            return content
        elif isinstance(content, _ComponentBase):
            return safe(content)
        elif isinstance(content, str):
            return escape(content)
        elif _is_iterable(content):
            escaped = self._escape(content)
            return safe("".join(escaped))
        else:
            return escape(content)

    @abc.abstractmethod
    def _get_content(self, children: safe) -> Union[ContentType, Iterable[ContentType]]:
        ...


class _FuncComponent(_ContentMixin, _LazyComponent):
    _func: Callable
    _pass_children: bool

    def _get_content(self, children: safe) -> Union[ContentType, Iterable[ContentType]]:
        args = tuple(self.props[argname] for argname in self._argnames)
        kwargs = {k: v for k, v in self.props.items() if k not in self._argnames}

        if self._pass_children:
            kwargs["children"] = children

        # self.func is unbound
        content = self.__class__._func(*args, **kwargs)
        return content


class _ClassComponent(_ContentMixin, _LazyComponent):
    _pass_children: bool
    _user_class: ComponentClass

    @cached_property
    def _user_instance(self) -> ComponentClass:
        return self._user_class(**self.props)

    def _get_content(self, children: safe):
        if self._pass_children:
            return self._user_instance.render(children)
        else:
            return self._user_instance.render()


class _HTMLComponentBase(_ComponentBase):
    _html_tag: str
    _attributes = None

    def __init__(self, **kwargs):
        if self._attributes is not None:
            kwargs.update(self._attributes)
        self._original_kwargs = kwargs

        new_kwargs, self._keyval_args, self._bool_args = self._convert_kwargs(kwargs)
        super().__init__(**new_kwargs)

    def replace(self, **kwargs) -> CompSelf:
        converted, _, _ = self._convert_kwargs(kwargs)
        return super().replace(**converted)

    def append(self, **kwargs) -> CompSelf:
        converted, _, _ = self._convert_kwargs(kwargs)
        return super().append(**converted)

    def merge(self, **kwargs) -> CompSelf:
        converted, _, _ = self._convert_kwargs(kwargs)
        return super().merge(**converted)

    def _convert_kwargs(self, kwargs) -> Tuple[dict, dict, list]:
        new_kwargs = {}
        keyval_args = {}
        bool_args = []

        for key, val in kwargs.items():
            if isinstance(val, str) and '"' in val and "'" in val:
                raise ValueError("Both single and double quotes in attribute value")
            elif keyword.iskeyword(no_underscore := key[:-1]):
                new_kwargs[no_underscore] = val
                keyval_args[no_underscore] = val
            elif isinstance(val, bool):
                if val:
                    bool_args.append(key)
                else:
                    # must not include in the output
                    continue
            else:
                new_kwargs[key] = val
                keyval_args[key] = val

        return new_kwargs, keyval_args, bool_args

    def _get_attributes(self) -> str:
        conv = lambda s: escape(str(s).replace("_", "-"))
        bool_args = " ".join(conv(a) for a in self._bool_args)
        bool_args = " " + bool_args if bool_args else ""
        kv_args = []
        for k, v in self._keyval_args.items():
            if v is None:
                continue
            html_key = conv(k)
            html_val = escape(v)
            if '"' in html_val:
                html_attr = f"{html_key}='{html_val}'"
            else:
                html_attr = f'{html_key}="{html_val}"'
            kv_args.append(html_attr)
        kv_args = " ".join(kv_args)
        kv_args = " " + kv_args if kv_args else ""
        return bool_args + kv_args


class _HTMLComponent(_HTMLComponentBase, _LazyComponent):
    def _render(self, children: safe) -> safe:
        attributes = self._get_attributes()
        return safe(f"<{self._html_tag}{attributes}>{children}</{self._html_tag}>")


class _SelfClosingHTMLComponent(_HTMLComponentBase):
    def __str__(self) -> safe:
        attributes = self._get_attributes()
        return safe(f"<{self._html_tag}{attributes} />")


def Component(
    func_or_class: Union[ComponentClass, Callable],
) -> Union[Type[_ClassComponent], Type[_FuncComponent]]:
    if inspect.isfunction(func_or_class):
        return _make_func_component(func_or_class)
    elif inspect.isclass(func_or_class):
        return _make_class_component(func_or_class)
    else:
        raise TypeError("Components can only be classes or functions")


def _make_defaults(argspec):
    if argspec.varargs is not None or argspec.varkw is not None:
        # We cannot save props and .copy(), .append(), .replace() wouldn't work
        raise TypeError("Components cannot have *args or **kwargs")

    if not argspec.args and not argspec.kwonlyargs:
        return None

    props = {}

    if argspec.defaults is not None:
        # Defaults are only for the last arguments
        names_with_defaults = argspec.args[-len(argspec.defaults) :]
        argdefaults = dict(zip(names_with_defaults, argspec.defaults))
        props.update(argdefaults)

    if argspec.kwonlydefaults is not None:
        kwdefaults = {k: v for k, v in argspec.kwonlydefaults.items() if v is not None}
        props.update(kwdefaults)

    return props


def _make_class_component(user_class: ComponentClass) -> Type[_ClassComponent]:
    if not hasattr(user_class, "render"):
        raise TypeError(f"{user_class.__name__} doesn't have a .render() method.")

    init_argspec = inspect.getfullargspec(user_class.__init__)
    init_argspec.args.remove("self")
    render_argspec = inspect.getfullargspec(user_class.render)
    pass_children = "children" in (render_argspec.args + render_argspec.kwonlyargs)
    return type(
        user_class.__name__,
        (_ClassComponent,),
        dict(
            _user_class=user_class,
            _argnames=tuple(init_argspec.args),
            _defaults=_make_defaults(init_argspec),
            _pass_children=pass_children,
            __module__=user_class.__module__,
        ),
    )


def _make_func_component(func: Callable) -> Type[_FuncComponent]:
    argspec = inspect.getfullargspec(func)
    pass_children = "children" in argspec.kwonlyargs
    return type(
        func.__name__,
        (_FuncComponent,),
        dict(
            _func=func,
            _argnames=tuple(argspec.args),
            _defaults=_make_defaults(argspec),
            _pass_children=pass_children,
            __module__=func.__module__,
        ),
    )


def _HtmlElem(html_tag: str, parent_class: T) -> T:
    return type(
        html_tag.capitalize(),
        (parent_class,),
        dict(
            _html_tag=html_tag,
            __module__="compone.html",
        ),
    )


def _Elem(html_tag: str) -> Type[_HTMLComponent]:
    """Create Component from HTML element on the fly."""
    return _HtmlElem(html_tag, _HTMLComponent)


def _SelfElem(html_tag: str) -> Type[_SelfClosingHTMLComponent]:
    """Create Component from self-closing HTML element on the fly."""
    return _HtmlElem(html_tag, _SelfClosingHTMLComponent)
