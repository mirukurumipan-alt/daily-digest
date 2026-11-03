# 毎日ダイジェスト

- `feeds.json` : 分野と情報源(RSS)。ここを編集すれば分野・媒体を増減できる
- `collect.py` : RSS取得→選別→(任意)Gemini要約 → `docs/data/news.json`
- `docs/` : PWA本体(GitHub Pagesで公開)
- `.github/workflows/daily.yml` : 毎朝5:30(JST)に自動更新

## ローカル確認
```
python collect.py          # データ生成
python -m http.server -d docs 8000   # http://localhost:8000
```

## 公開手順
1. GitHubにリポジトリを作成し、このフォルダをアップロード
2. Settings > Pages で、Branch=main / Folder=/docs を選択
3. (任意) Google AI StudioでAPIキーを取得し、Settings > Secrets > Actions に `GEMINI_API_KEY` として登録
4. Actions > daily-digest > Run workflow で初回実行
5. スマホでURLを開き「ホーム画面に追加」
