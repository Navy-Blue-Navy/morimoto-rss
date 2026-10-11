import requests
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET

from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime


SOURCE_URL = "https://morimotohifuka.mdja.jp/"

BASE_DIR = Path(__file__).parent
OUTPUT = BASE_DIR / "morimoto.xml"
STATE_FILE = BASE_DIR / "morimoto_state.json"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
}

DATE_RE = re.compile(
    r"(20\d{2})年(\d{1,2})月(\d{1,2})日"
)


# --------------------------------------------------
# URLからセッションらしき s= を除去
# --------------------------------------------------

def clean_url(url):

    parts = urlsplit(url)

    query = [
        (k, v)
        for k, v in parse_qsl(
            parts.query,
            keep_blank_values=True
        )
        if k != "s"
    ]

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query),
        parts.fragment
    ))


# --------------------------------------------------
# 既存RSS読み込み
# --------------------------------------------------

old_items = {}

if OUTPUT.exists():

    try:

        tree = ET.parse(OUTPUT)

        for item in tree.getroot().findall(
            "./channel/item"
        ):

            guid = item.findtext(
                "guid",
                ""
            )

            if not guid:
                continue

            old_items[guid] = {
                "title": item.findtext(
                    "title",
                    ""
                ),
                "link": item.findtext(
                    "link",
                    ""
                ),
                "description": item.findtext(
                    "description",
                    ""
                ),
                "pubDate": item.findtext(
                    "pubDate",
                    ""
                ),
                "guid": guid,
            }

    except Exception as e:

        print(
            "既存RSS読み込みエラー:",
            e
        )

        print(
            "安全のため今回は更新しません。"
        )

        raise SystemExit(0)


print(
    "既存RSS件数:",
    len(old_items)
)


# --------------------------------------------------
# 臨時告知の前回状態
# --------------------------------------------------

old_notice = None

if STATE_FILE.exists():

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        old_notice = state.get(
            "notice"
        )

    except Exception as e:

        print(
            "状態ファイル読み込みエラー:",
            e
        )

        print(
            "安全のため今回は更新しません。"
        )

        raise SystemExit(0)


# --------------------------------------------------
# ページ取得
# --------------------------------------------------

response = None

MAX_RETRIES = 3


for attempt in range(
    1,
    MAX_RETRIES + 1
):

    try:

        print()
        print(
            f"もりもと皮膚科 お知らせ取得 "
            f"{attempt}/{MAX_RETRIES}"
        )

        response = requests.get(
            SOURCE_URL,
            headers=HEADERS,
            timeout=30
        )

        print(
            "HTTP:",
            response.status_code
        )

        response.raise_for_status()

        break

    except requests.RequestException as e:

        print(
            "取得失敗:",
            e
        )

        response = None

        if attempt < MAX_RETRIES:

            print(
                "10秒待って再試行します。"
            )

            time.sleep(10)


if response is None:

    print()
    print(
        "ページを取得できませんでした。"
    )

    print(
        "既存XML・状態ファイルを維持します。"
    )

    raise SystemExit(0)


# --------------------------------------------------
# HTML解析
# --------------------------------------------------

soup = BeautifulSoup(
    response.content,
    "html.parser"
)


# --------------------------------------------------
# 通常のお知らせ一覧
# --------------------------------------------------

article_blocks = soup.select(
    "div.information-list-item"
)

print()
print(
    "通常記事候補:",
    len(article_blocks),
    "件"
)


if len(article_blocks) == 0:

    print(
        "お知らせ一覧を取得できませんでした。"
    )

    print(
        "ページ構造変更の可能性があります。"
    )

    print(
        "既存XML・状態ファイルを維持します。"
    )

    raise SystemExit(0)


current_items = []


for block in article_blocks:

    text = block.get_text(
        " ",
        strip=True
    )

    match = DATE_RE.search(text)

    if not match:
        continue


    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))

    date_text = match.group(0)


    a = block.find(
        "a",
        href=True
    )

    if a is None:
        continue


    title = a.get_text(
        " ",
        strip=True
    )

    if not title:
        continue


    raw_link = urljoin(
        SOURCE_URL,
        a["href"]
    )

    link = clean_url(
        raw_link
    )


    pub_dt = datetime(
        year,
        month,
        day,
        12,
        0,
        0,
        tzinfo=JST
    )


    # cidを含む固定化URLをGUIDの基礎にする
    guid_source = (
        "article|"
        + date_text
        + "|"
        + title
        + "|"
        + link
    )

    guid = hashlib.sha256(
        guid_source.encode("utf-8")
    ).hexdigest()


    current_items.append({
        "title": title,
        "link": link,
        "description": (
            "もりもと皮膚科クリニック "
            "お知らせ"
        ),
        "pubDate": format_datetime(
            pub_dt
        ),
        "guid": guid,
    })


# --------------------------------------------------
# 通常記事の重複除去
# --------------------------------------------------

unique_items = []
seen = set()


for item in current_items:

    if item["guid"] in seen:
        continue

    seen.add(
        item["guid"]
    )

    unique_items.append(
        item
    )


current_items = unique_items


print(
    "通常記事取得:",
    len(current_items),
    "件"
)


if len(current_items) == 0:

    print(
        "有効な通常記事を取得できませんでした。"
    )

    print(
        "既存XML・状態ファイルを維持します。"
    )

    raise SystemExit(0)


# --------------------------------------------------
# トップの「※お知らせ※」取得
# --------------------------------------------------

notice_text = None


for text_node in soup.find_all(
    string=True
):

    text = text_node.strip()

    if "※お知らせ※" not in text:
        continue


    element = text_node.parent

    # 調査結果:
    # text → span → strong → span
    #
    # 3階層上が臨時告知＋直後の案内を含む範囲
    for _ in range(2):

        if element is not None:
            element = element.parent


    if element is not None:

        notice_text = element.get_text(
            " ",
            strip=True
        )

        notice_text = re.sub(
            r"\s+",
            " ",
            notice_text
        ).strip()

    break


print()
print("トップ臨時告知:")

if notice_text:

    print(notice_text)

else:

    print(
        "「※お知らせ※」は見つかりませんでした。"
    )


# --------------------------------------------------
# 臨時告知の変更判定
# --------------------------------------------------

notice_item = None
notice_changed = False


if notice_text:

    # 初回実行
    if old_notice is None:

        print()
        print(
            "臨時告知の初回基準値を保存します。"
        )

        print(
            "初回なのでRSS新着にはしません。"
        )


    # 2回目以降で内容が変わった
    elif notice_text != old_notice:

        notice_changed = True

        print()
        print(
            "臨時告知の変更を検出しました。"
        )

        now = datetime.now(
            JST
        )

        notice_guid_source = (
            "notice|"
            + notice_text
        )

        notice_guid = hashlib.sha256(
            notice_guid_source.encode(
                "utf-8"
            )
        ).hexdigest()


        notice_item = {
            "title": (
                "トップのお知らせが更新されました"
            ),
            "link": SOURCE_URL,
            "description": notice_text,
            "pubDate": format_datetime(
                now
            ),
            "guid": notice_guid,
        }


    else:

        print()
        print(
            "臨時告知に変更はありません。"
        )


# --------------------------------------------------
# RSS統合
# --------------------------------------------------

all_items = []
seen_guids = set()


# 臨時告知変更があれば最初に追加
if notice_item is not None:

    all_items.append(
        notice_item
    )

    seen_guids.add(
        notice_item["guid"]
    )


# 現在の通常記事
for item in current_items:

    if item["guid"] in seen_guids:
        continue

    all_items.append(
        item
    )

    seen_guids.add(
        item["guid"]
    )


# 過去RSSを保持
for guid, item in old_items.items():

    if guid in seen_guids:
        continue

    all_items.append(
        item
    )

    seen_guids.add(
        guid
    )


# --------------------------------------------------
# 日付順
# --------------------------------------------------

def get_date(item):

    try:

        return parsedate_to_datetime(
            item["pubDate"]
        )

    except Exception:

        return datetime.min.replace(
            tzinfo=timezone.utc
        )


all_items.sort(
    key=get_date,
    reverse=True
)


# --------------------------------------------------
# RSS生成
# --------------------------------------------------

rss = ET.Element(
    "rss",
    version="2.0"
)

channel = ET.SubElement(
    rss,
    "channel"
)


ET.SubElement(
    channel,
    "title"
).text = (
    "もりもと皮膚科クリニック お知らせ"
)


ET.SubElement(
    channel,
    "link"
).text = SOURCE_URL


ET.SubElement(
    channel,
    "description"
).text = (
    "もりもと皮膚科クリニックの"
    "お知らせ一覧・トップ臨時告知"
)


ET.SubElement(
    channel,
    "language"
).text = "ja"


# --------------------------------------------------
# RSS記事
# --------------------------------------------------

for item in all_items:

    element = ET.SubElement(
        channel,
        "item"
    )


    ET.SubElement(
        element,
        "title"
    ).text = item["title"]


    ET.SubElement(
        element,
        "link"
    ).text = item["link"]


    ET.SubElement(
        element,
        "description"
    ).text = item["description"]


    ET.SubElement(
        element,
        "pubDate"
    ).text = item["pubDate"]


    guid_element = ET.SubElement(
        element,
        "guid"
    )

    guid_element.set(
        "isPermaLink",
        "false"
    )

    guid_element.text = item["guid"]


# --------------------------------------------------
# XML保存
# --------------------------------------------------

tree = ET.ElementTree(
    rss
)

ET.indent(
    tree,
    space="  "
)

tree.write(
    OUTPUT,
    encoding="utf-8",
    xml_declaration=True
)


# --------------------------------------------------
# 状態ファイル保存
# --------------------------------------------------

with open(
    STATE_FILE,
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        {
            "notice": notice_text
        },
        f,
        ensure_ascii=False,
        indent=2
    )


# --------------------------------------------------
# 結果表示
# --------------------------------------------------

print()
print("RSS作成成功")

print(
    "RSS保存件数:",
    len(all_items),
    "件"
)

print(
    "RSS保存先:",
    OUTPUT
)

print(
    "状態保存先:",
    STATE_FILE
)


print()
print("今回取得した通常記事:")


for i, item in enumerate(
    current_items,
    start=1
):

    print()

    print(
        f"[{i}] "
        f"{item['pubDate']} "
        f"{item['title']}"
    )

    print(
        "    ",
        item["link"]
    )


if notice_changed:

    print()
    print(
        "★ トップ臨時告知の変更を"
        "RSSへ追加しました。"
    )