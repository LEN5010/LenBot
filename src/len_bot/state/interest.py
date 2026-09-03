from pydantic import BaseModel, Field

class InterestModel(BaseModel):
    """Explicit interest weights (§85) used for feed filtering and initiative scoring."""
    
    # Topic -> weight (0.0 to 1.0)
    topics: dict[str, float] = Field(default_factory=lambda: {
        "live": 0.9,
        "gaming": 0.8,
        "esports": 0.7,
        "tech": 0.6,
        "food": 0.5
    })
    
    # Keywords mapping to topics
    topic_keywords: dict[str, list[str]] = Field(default_factory=lambda: {
        "live": ["直播", "开播", "主播", "切片", "b站", "推流"],
        "gaming": ["游戏", "steam", "打折", "黑猴", "瓦罗兰特", "cs2"],
        "esports": ["比赛", "夺冠", "决赛", "淘汰赛", "major", "s赛"],
        "tech": ["ai", "大模型", "python", "代码", "agent", "开源"],
        "food": ["火锅", "烧烤", "奶茶", "夜宵", "外卖"]
    })

    def score_text(self, text: str) -> tuple[float, list[str]]:
        text_lower = text.lower()
        matched = []
        max_score = 0.0

        for topic, keywords in self.topic_keywords.items():
            if any(k in text_lower for k in keywords):
                matched.append(topic)
                weight = self.topics.get(topic, 0.5)
                if weight > max_score:
                    max_score = weight

        return max_score, matched
