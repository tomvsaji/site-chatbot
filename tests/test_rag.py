import tempfile
import unittest
from pathlib import Path

from rag import BM25Index, load_chunks, tokenize


class RetrievalTests(unittest.TestCase):
    def test_tokenize_normalizes_words(self):
        self.assertEqual(tokenize("Hello, HELLO world!"), ("hello", "hello", "world"))

    def test_load_and_find_relevant_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "services.md").write_text(
                "I build automation workflows with n8n.", encoding="utf-8"
            )
            (root / "hobbies.md").write_text(
                "My favorite hobby is landscape photography.", encoding="utf-8"
            )
            index = BM25Index(load_chunks(root))
            results = index.search("What automation services are offered?")
            self.assertTrue(results)
            self.assertEqual(results[0][0].source, "services.md")

    def test_ignores_unsupported_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "secret.env").write_text("PASSWORD=example", encoding="utf-8")
            self.assertEqual(load_chunks(root), [])

    def test_markdown_chunks_preserve_metadata_headings_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = " ".join(f"word{number}" for number in range(400))
            (root / "article.md").write_text(
                "# Useful Article\n\n"
                "Source: https://example.com/article\n\n"
                "## Important Section\n\n"
                f"{body}\n\n"
                "Home\n\n© 2026 Example",
                encoding="utf-8",
            )
            chunks = load_chunks(root)
            self.assertGreater(len(chunks), 1)
            self.assertTrue(all(len(chunk.tokens) <= 140 for chunk in chunks))
            self.assertTrue(all(chunk.title == "Useful Article" for chunk in chunks))
            self.assertTrue(
                all(chunk.heading == "Important Section" for chunk in chunks)
            )
            self.assertTrue(
                all(chunk.url == "https://example.com/article" for chunk in chunks)
            )
            self.assertFalse(any("© 2026" in chunk.text for chunk in chunks))

    def test_bm25_ignores_name_and_question_stopwords(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "profile.md").write_text(
                "# Tom Profile\n\nTom builds AI systems.", encoding="utf-8"
            )
            index = BM25Index(load_chunks(root))
            self.assertEqual(index.search("What is Tom's favorite food?"), [])


if __name__ == "__main__":
    unittest.main()
