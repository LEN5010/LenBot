# Native SQLite FTS5 with Trigram Tokenizer for CJK Search

Chinese text retrieval in SQLite typically tempts teams toward external C extensions (`jieba_sqllite`) or vector databases. We decided to use SQLite's native `trigram` tokenizer (`tokenize='trigram'`) built into SQLite 3.34+. This provides zero-dependency CJK substring matching across all platforms without dictionary maintenance or compile-time dependencies, trading modest index disk overhead for complete operational simplicity.
