"""기존 board_posts 데이터의 응답 변환. HTTP 경로는 posts.py에서만 등록한다."""
from core import iso
from db import BoardPost


def post_out(post: BoardPost, detail=False):
    data = {
        "id": post.id, "author": {"id": f"sample:{post.id}" if post.is_sample else None,
                                  "nickname": post.author_name, "avatarUrl": None},
        "festivalId": post.festival_id, "festivalName": post.festival_name, "region": post.region,
        "title": post.title, "coverUrl": None, "excerpt": post.body[:130],
        "likeCount": 0, "commentCount": 0, "likedByMe": False,
        "createdAt": iso(post.created_at), "isSample": post.is_sample,
    }
    if detail:
        data.update(body=post.body, images=[], placeIds=[], visitedOn=post.visited_on, version=1)
    return data


