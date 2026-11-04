# 宣伝判定の比較

宣伝・告知 26 文 / スピーチ 73 文(3セット合計)

| 方法 | 正しくカット | 誤カット(スピーチを切った) | 見逃し(宣伝が残った) | 適合率 | 再現率 | 時間 |
|---|---|---|---|---|---|---|
| 今の仕組み(キーワード + qwen2.5 + 安全装置) | 26 | 0 | 0 | 100% | 100% | 34秒 |
| 前の仕組み(qwen2.5 をそのまま信用) | 25 | 34 | 1 | 42% | 96% | 59秒 |
| Flan-T5 base(250M) | 0 | 0 | 26 | 0% | 0% | 5秒 |
| Flan-T5 large(780M) | 20 | 1 | 6 | 95% | 77% | 9秒 |
| Flan-T5 large + 同じ安全装置 | 23 | 0 | 3 | 100% | 88% | 8秒 |

## セット別(正しくカット / 誤カット / 見逃し)

| 方法 | struggle | adtest | tricky |
|---|---|---|---|
| 今の仕組み(キーワード + qwen2.5 + 安全装置) | 1 / 0 / 0 | 5 / 0 / 0 | 20 / 0 / 0 |
| 前の仕組み(qwen2.5 をそのまま信用) | 1 / 12 / 0 | 5 / 2 / 0 | 19 / 20 / 1 |
| Flan-T5 base(250M) | 0 / 0 / 1 | 0 / 0 / 5 | 0 / 0 / 20 |
| Flan-T5 large(780M) | 0 / 1 / 1 | 3 / 0 / 2 | 17 / 0 / 3 |
| Flan-T5 large + 同じ安全装置 | 1 / 0 / 0 | 4 / 0 / 1 | 18 / 0 / 2 |

## 間違えた文

- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「See many people look at struggle like it's a bad thing.」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「Your struggle will be the gift that the world falls in love with.」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「Nobody wants to hear the story of the man that was giving everything.」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「People want to hear the stories of man and women that endured pain and turned their struggles into a gift.」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「Do you want people just to feel sorry for you?」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「Or you want people to look up to you?」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「Do you want to be a legend?」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「Because legends are born in a valley of struggle.」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「Fear is only a fictional movie, but reality is a struggle.」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「Reality is the struggle.」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「So take the struggle and change the reality.」
- 前の仕組み(qwen2.5 をそのまま信用) / struggle: 誤カット「Because the whole time you were thinking that your struggle was holding you back, it was really giving you the tools to become one of the greatest.」
- 前の仕組み(qwen2.5 をそのまま信用) / adtest: 誤カット「We all have the opportunity to be better and do better than we did yesterday.」
- 前の仕組み(qwen2.5 をそのまま信用) / adtest: 誤カット「If you take responsibility for your life and seek the opportunities you wish to find.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 見逃し「Audible is offering a free thirty day trial.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Subscribe to your own goals, not to other people's opinions.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Invest in yourself before anything else.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Download a new mindset every single morning.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Follow your dreams even when nobody believes in you.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Like a lion, you have to hunt for what you want.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「The price of greatness is responsibility.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Nobody is coming to save you.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Your struggle is your gift.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Wake up at five and go to work on yourself.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Money will not make you happy, purpose will.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Thank you, pain, for making me stronger.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Check your excuses at the door.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Share your story with the world, it might save someone.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「You were born to win.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Comment less on other people's lives and build your own.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「The bell rings and the fight begins.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Watch what happens when you refuse to quit.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Every video of your life is still being recorded.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Stay focused, stay hungry, stay humble.」
- 前の仕組み(qwen2.5 をそのまま信用) / tricky: 誤カット「Today is the day you stop making excuses.」
- Flan-T5 base(250M) / struggle: 見逃し「Thank you for watching.」
- Flan-T5 base(250M) / adtest: 見逃し「Hey guys, before we start, download the karma app.」
- Flan-T5 base(250M) / adtest: 見逃し「The link is in the description.」
- Flan-T5 base(250M) / adtest: 見逃し「Use promo code motivation for 20 % of your first year.」
- Flan-T5 base(250M) / adtest: 見逃し「Thanks for watching.」
- Flan-T5 base(250M) / adtest: 見逃し「Don't forget to subscribe and hit the notification bell.」
- Flan-T5 base(250M) / tricky: 見逃し「Hey guys, welcome back to the channel.」
- Flan-T5 base(250M) / tricky: 見逃し「Before we get started, a quick word from today's sponsor.」
- Flan-T5 base(250M) / tricky: 見逃し「This video is brought to you by BetterHelp.」
- Flan-T5 base(250M) / tricky: 見逃し「Head over to the link in the description.」
- Flan-T5 base(250M) / tricky: 見逃し「Use code RISE at checkout to get 15 percent off.」
- Flan-T5 base(250M) / tricky: 見逃し「Download the app for free on the App Store.」
- Flan-T5 base(250M) / tricky: 見逃し「If you enjoyed this video, hit the like button.」
- Flan-T5 base(250M) / tricky: 見逃し「Make sure you subscribe and turn on notifications.」
- Flan-T5 base(250M) / tricky: 見逃し「Follow us on Instagram for daily motivation.」
- Flan-T5 base(250M) / tricky: 見逃し「Check out our new merch store.」
- Flan-T5 base(250M) / tricky: 見逃し「Let me know in the comments what you thought.」
- Flan-T5 base(250M) / tricky: 見逃し「Thanks so much for watching, see you next time.」
- Flan-T5 base(250M) / tricky: 見逃し「Our podcast is available on Spotify every Monday.」
- Flan-T5 base(250M) / tricky: 見逃し「Sign up for the free newsletter on our website.」
- Flan-T5 base(250M) / tricky: 見逃し「Audible is offering a free thirty day trial.」
- Flan-T5 base(250M) / tricky: 見逃し「Today's episode is sponsored by Squarespace.」
- Flan-T5 base(250M) / tricky: 見逃し「Don't forget to share this video with a friend.」
- Flan-T5 base(250M) / tricky: 見逃し「Support the channel on Patreon.」
- Flan-T5 base(250M) / tricky: 見逃し「Grab your copy of the book, link below.」
- Flan-T5 base(250M) / tricky: 見逃し「That's it for today's video, peace out.」
- Flan-T5 large(780M) / struggle: 誤カット「So don't you give up.」
- Flan-T5 large(780M) / struggle: 見逃し「Thank you for watching.」
- Flan-T5 large(780M) / adtest: 見逃し「Hey guys, before we start, download the karma app.」
- Flan-T5 large(780M) / adtest: 見逃し「The link is in the description.」
- Flan-T5 large(780M) / tricky: 見逃し「Hey guys, welcome back to the channel.」
- Flan-T5 large(780M) / tricky: 見逃し「Let me know in the comments what you thought.」
- Flan-T5 large(780M) / tricky: 見逃し「That's it for today's video, peace out.」
- Flan-T5 large + 同じ安全装置 / adtest: 見逃し「Hey guys, before we start, download the karma app.」
- Flan-T5 large + 同じ安全装置 / tricky: 見逃し「Let me know in the comments what you thought.」
- Flan-T5 large + 同じ安全装置 / tricky: 見逃し「That's it for today's video, peace out.」
