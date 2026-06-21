# skills.py
# 다양한 유틸리티 함수들을 모아두는 파일입니다.
# 새로운 skill(기능)이 필요하면 이 파일에 함수를 추가하세요.

import requests


def search_news(keyword: str, country: str = "us,kr") -> dict:
    """
    NewsData.io API를 사용하여 뉴스를 검색합니다.

    Args:
        keyword: 검색할 키워드 (예: "AI", "경제", "스포츠")
        country: 검색 대상 국가 코드, 쉼표로 구분 (기본값: "us,kr")

    Returns:
        dict: API 응답 JSON 데이터
              - status: 응답 상태 ("success" 또는 에러)
              - totalResults: 총 검색 결과 수
              - results: 뉴스 기사 리스트
                  각 기사에는 title, description, link, pubDate 등 포함

    Example:
        >>> data = search_news("AI")
        >>> for article in data.get("results", []):
        ...     print(article["title"])
    """
    API_KEY = "pub_92735e51c2e149878fd49e7675188dc1"

    url = "https://newsdata.io/api/1/latest"
    params = {
        "apikey": API_KEY,
        "q": keyword,
        "country": country,
    }

    response = requests.get(url, params=params)
    data = response.json()

    return data


# ---------------------------------------------------------------------------
# 아래는 이 파일을 직접 실행했을 때 테스트용으로 동작하는 코드입니다.
# 다른 파일에서 import 해서 사용할 때는 실행되지 않습니다.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    keyword = input("검색할 뉴스 키워드를 입력하세요: ")
    result = search_news(keyword)

    if result.get("status") == "success":
        articles = result.get("results", [])
        print(f"\n총 {len(articles)}개의 뉴스를 찾았습니다.\n")

        for i, article in enumerate(articles, 1):
            title = article.get("title", "제목 없음")
            description = article.get("description", "설명 없음")
            link = article.get("link", "")
            pub_date = article.get("pubDate", "날짜 없음")

            print(f"[{i}] {title}")
            print(f"    날짜: {pub_date}")
            print(f"    설명: {description[:100]}..." if description and len(description) > 100 else f"    설명: {description}")
            print(f"    링크: {link}")
            print()
    else:
        print("뉴스 검색에 실패했습니다.")
        print(result)
