import json
import unittest

from bot.instagram_embed import instagram_shortcode, parse_embed_html


def _embed_html(media: dict) -> str:
    context = json.dumps({"gql_data": {"shortcode_media": media}})
    return '<script>{"contextJSON":' + json.dumps(context) + "}</script>"


class InstagramEmbedTests(unittest.TestCase):
    def test_shortcodes_are_parsed_from_post_urls(self) -> None:
        self.assertEqual(
            instagram_shortcode(
                "https://www.instagram.com/p/De35bidDrng/?mdxt=abc"
            ),
            "De35bidDrng",
        )
        self.assertEqual(
            instagram_shortcode("https://instagram.com/reel/Abc_123/"),
            "Abc_123",
        )
        self.assertIsNone(
            instagram_shortcode("https://instagram.com.evil.example/p/De35bidDrng/")
        )
        self.assertIsNone(
            instagram_shortcode("https://www.instagram.com/havasosyalmedya/")
        )

    def test_carousel_images_are_extracted_and_videos_skipped(self) -> None:
        media = {
            "__typename": "GraphSidecar",
            "shortcode": "De35bidDrng",
            "is_video": False,
            "display_url": "https://cdn.example/cover.jpg",
            "owner": {"username": "havasosyalmedya"},
            "edge_media_to_caption": {
                "edges": [{"node": {"text": "Başlık\nikinci satır"}}]
            },
            "edge_sidecar_to_children": {
                "edges": [
                    {
                        "node": {
                            "is_video": False,
                            "display_url": "https://cdn.example/1.jpg?x=1",
                        }
                    },
                    {
                        "node": {
                            "is_video": True,
                            "display_url": "https://cdn.example/video-thumb.jpg",
                        }
                    },
                    {
                        "node": {
                            "is_video": False,
                            "display_url": "https://cdn.example/2.webp",
                        }
                    },
                ]
            },
        }

        items = parse_embed_html(_embed_html(media), 10)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "https://cdn.example/1.jpg?x=1")
        self.assertEqual(items[0]["extension"], "jpg")
        self.assertEqual(items[0]["description"], "Başlık\nikinci satır")
        self.assertEqual(items[0]["username"], "havasosyalmedya")
        self.assertEqual(items[1]["extension"], "webp")

    def test_single_video_is_not_returned(self) -> None:
        media = {
            "__typename": "GraphVideo",
            "is_video": True,
            "display_url": "https://cdn.example/thumb.jpg",
        }
        self.assertEqual(parse_embed_html(_embed_html(media), 10), [])

    def test_limit_and_missing_context(self) -> None:
        media = {
            "is_video": False,
            "display_url": "https://cdn.example/1.jpg",
        }
        self.assertEqual(len(parse_embed_html(_embed_html(media), 1)), 1)
        self.assertEqual(parse_embed_html("<html>login</html>", 10), [])


if __name__ == "__main__":
    unittest.main()
