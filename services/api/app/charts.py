from io import BytesIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def render_chart(csv_data: bytes, chart_type: str = "line") -> tuple[bytes, str, str, dict]:
    frame = pd.read_csv(BytesIO(csv_data))
    if frame.empty or len(frame.columns) < 2:
        raise ValueError("CSV needs at least two columns and one data row")
    numeric = frame.select_dtypes(include="number")
    if numeric.empty and chart_type != "table":
        raise ValueError("CSV needs a numeric column")
    if chart_type in ("radar", "3d") and len(numeric.columns) < 3:
        raise ValueError("Radar and 3D charts need at least three numeric columns")
    x = frame.columns[0]
    y = (
        next((name for name in numeric.columns if name != x), numeric.columns[0])
        if not numeric.empty
        else x
    )
    if x == y:
        x = "row"
    x_values = frame.iloc[:, 0] if x != "row" else frame.index
    if chart_type == "3d":
        fig = plt.figure(figsize=(8, 5), layout="constrained")
        ax = fig.add_subplot(projection="3d")
        cols = list(numeric.columns[:3])
        ax.scatter(*[frame[col] for col in cols])
        ax.set(xlabel=cols[0], ylabel=cols[1], zlabel=cols[2])
        script = (
            "import pandas as pd\nimport matplotlib.pyplot as plt\n"
            "df = pd.read_csv('data.csv')\nfig = plt.figure()\nax = fig.add_subplot(projection='3d')\n"
            f"ax.scatter(df[{cols[0]!r}], df[{cols[1]!r}], df[{cols[2]!r}])\n"
            "plt.savefig('chart.png', dpi=180)\n"
        )
        caption = f"3D scatter of {', '.join(cols)} across {len(frame)} observations."
    elif chart_type == "radar":
        import numpy as np

        cols = list(numeric.columns)
        angles = np.linspace(0, 2 * np.pi, len(cols), endpoint=False).tolist()
        values = [float(frame[col].iloc[0]) for col in cols]
        fig, ax = plt.subplots(
            figsize=(7, 6), subplot_kw={"projection": "polar"}, layout="constrained"
        )
        ax.plot(angles + angles[:1], values + values[:1], marker="o")
        ax.fill(angles + angles[:1], values + values[:1], alpha=0.15)
        ax.set_xticks(angles, cols)
        script = (
            "import pandas as pd\nimport numpy as np\nimport matplotlib.pyplot as plt\n"
            "df = pd.read_csv('data.csv')\n"
            f"cols = {cols!r}\nangles = np.linspace(0, 2*np.pi, len(cols), endpoint=False).tolist()\n"
            "values = [float(df[c].iloc[0]) for c in cols]\n"
            "fig, ax = plt.subplots(subplot_kw={'projection':'polar'})\n"
            "ax.plot(angles+angles[:1], values+values[:1])\nax.set_xticks(angles, cols)\n"
            "plt.savefig('chart.png', dpi=180)\n"
        )
        caption = (
            "Radar profile for the first observation: "
            + ", ".join(f"{col}={value:g}" for col, value in zip(cols, values, strict=True))
            + "."
        )
    elif chart_type == "table":
        fig, ax = plt.subplots(figsize=(10, min(9, 1 + len(frame) * 0.35)), layout="constrained")
        ax.axis("off")
        ax.table(cellText=frame.head(20).values, colLabels=frame.columns, loc="center")
        script = (
            "import pandas as pd\nimport matplotlib.pyplot as plt\n"
            "df = pd.read_csv('data.csv')\nfig, ax = plt.subplots()\nax.axis('off')\n"
            "ax.table(cellText=df.head(20).values, colLabels=df.columns, loc='center')\n"
            "plt.savefig('chart.png', dpi=180)\n"
        )
        caption = f"Table of {min(len(frame), 20)} shown rows from {len(frame)} observations."
    else:
        fig, ax = plt.subplots(figsize=(8, 5), layout="constrained")
        if chart_type == "bar":
            ax.bar(x_values, frame[y])
        elif chart_type == "scatter":
            ax.scatter(x_values, frame[y])
        else:
            ax.plot(x_values, frame[y], marker="o", linewidth=1.8)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        ax.grid(alpha=0.2)
        first, last = frame[y].iloc[0], frame[y].iloc[-1]
        direction = (
            "increased" if last > first else "decreased" if last < first else "was unchanged"
        )
        caption = f"{y} {direction} from {first:g} to {last:g} across {len(frame)} observations."
        x_arg = f"x={x!r}, " if x != "row" else ""
        script = (
            "import pandas as pd\nimport matplotlib.pyplot as plt\n"
            "df = pd.read_csv('data.csv')\n"
            f"df.plot({x_arg}y={y!r}, kind={chart_type!r})\n"
            "plt.tight_layout()\nplt.savefig('chart.png', dpi=180)\n"
        )
    image = BytesIO()
    fig.savefig(image, format="png", dpi=180)
    plt.close(fig)
    return image.getvalue(), script, caption, {"rows": len(frame), "x": x, "y": y}
