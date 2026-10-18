"""Conservative 2K-to-Flow-preview matching.

Both previews and 2K exports can differ in crop, overlay, and compression.
The score is normalized RGB RMSE: 0 is exact, smaller is better.
An unclear pair always remains unmatched; NEVER infer identity by download order.
"""
from io import BytesIO
from PIL import Image, ImageFilter, ImageOps

SIZE = (48, 27)


def vector(image):
    im = ImageOps.fit(image.convert('RGB'), SIZE, method=Image.Resampling.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(0.5))
    return list(im.tobytes())


def difference(a, b):
    return (sum((x-y)**2 for x, y in zip(a, b)) / len(a) / 65025) ** 0.5


def confident_scores(scored):
    """Decide from all candidates, not from whichever 2K request was last clicked.

    Tuned to accept clear preview/2K comparisons such as 0.272 vs 0.355,
    but reject near-ties such as 0.199 vs 0.219 or 0.158 vs 0.176.
    """
    if not scored:
        return None
    ranked = sorted(scored)
    best, number = ranked[0]
    if len(ranked) == 1:
        # One reference is not sufficient evidence for a very different image.
        return number if best <= 0.24 else None
    runner_up = ranked[1][0]
    gap = runner_up - best
    if best <= 0.32 and gap >= 0.065 and best <= runner_up * 0.82:
        return number
    return None


def match_image(file, refs):
    """refs maps visual number -> screenshot bytes. Return (visual # | None, scores)."""
    try:
        with Image.open(file) as im:
            target = vector(im)
        scored = []
        for num, blob in refs.items():
            with Image.open(BytesIO(blob)) as im:
                scored.append((difference(target, vector(im)), num))
    except Exception:
        return None, []
    scored.sort()
    return confident_scores(scored), scored
