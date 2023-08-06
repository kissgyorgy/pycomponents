from pycomp import make_component
from pycomp.html import Sel


@make_component
def Layout(title: str, children: str):
    return f"""
        <html>
            <head>
                <title>{title}</title>
            </head>
            <body>
                {children}
            </body>
        </html>
    """


@make_component
def UnordList(elems: list, children: str):
    namesli = "\n".join(f"<li>{elem}</li>" for elem in elems)
    return f"""
        <ul>
            {namesli}
        </ul>
    """


@make_component
def P(children: str):
    return f"<p>{children}</p>"


@make_component
def Ul(children):
    return f"<ul>{children}</ul>"


@make_component
def Li(children):
    return f"<li>{children}</li>"


@make_component
def H(level: int, children: str):
    elem = f"h{level}"
    return f"<{elem}>{children}</{elem}>"


@make_component
def Div(children: str):
    return f"""
        <div>
            {children}
        </div>
    """


def main():
    names = ["György", "Dóri1", "Dóri2"]
    title = "Page title"

    return Layout(title=title)[
        P()[title],
        "<div>HTML string</div>",
        Ul()[
            Li()["first elem"],
            Li()["second elem"],
        ],
        Sel("hr"),
        UnordList(names),
        Div()[
            H(1)[title],
            P()["paragraph"],
            "<br>",
        ],
        Div()[
            P()["first paragraph"],
            P()["second paragraph"],
        ],
    ]


print(main())
