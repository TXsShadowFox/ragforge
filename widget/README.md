# Chat widget

A chat bubble for any website, added with one tag. It answers questions from your
documents, with sources ("rules.pdf, page 4"), and visitors can rate each answer.

```html
<script src="https://YOUR-API/widget.js" data-api-key="rf_pub_..." async></script>
```

It is one plain JavaScript file ([widget.js](widget.js)): no libraries and no build step.
The API serves it at `GET /widget.js`.

## Setup

1. In the dashboard, open **API keys** and create a **public** key. Add every website that
   will show the chat as an allowed origin, like `https://www.example.com`.
2. Copy the tag that the dashboard shows into your pages, before `</body>`.

| Attribute       | Required | What it does                                                                           |
| --------------- | -------- | -------------------------------------------------------------------------------------- |
| `data-api-key`  | yes      | A public key (`rf_pub_...`). The widget refuses secret keys (`rf_live_...`).           |
| `data-title`    | no       | The panel's title. Default: "Ask a question".                                          |
| `data-greeting` | no       | The first message. Default: "Hi! Ask me anything about our documents."                 |
| `data-color`    | no       | Any CSS color for the bubble and the header. Pick a dark one: the text on it is white. |
| `data-api-url`  | no       | The API address. Default: where `widget.js` was loaded from.                           |

From your own JavaScript: `RAGForgeWidget.open()`, `RAGForgeWidget.close()`, `RAGForgeWidget.toggle()`.

## Safety

- **A public key is public:** anyone can read it in your page. It only works for the
  chat and for ratings, and only from your allowed websites (the browser's `Origin`
  header). Programs outside a browser can fake that header, so rate limits protect the
  key too:
  - the whole website: 10 questions a minute on the free plan
  - each visitor (IP address): 5 questions a minute
- **Anyone who can see your site can ask about your documents.** Upload only documents
  you are happy to answer questions about in public.
- **Answers are shown as plain text, never as HTML**, so text inside a document cannot
  run code on your website.
- Everything lives in a Shadow DOM: your CSS cannot break the widget, and the widget
  cannot change your page.
- The conversation is kept in the browser tab (`sessionStorage`), so it survives page
  changes on your site. "New chat" clears it.

## Try it locally

1. Start everything: `make up` (or `make dev`, `make worker` and `make frontend`).
2. In the dashboard (http://localhost:3000), upload a document and create a public key
   with the allowed origin `http://localhost:5500`.
3. `make widget-demo`, then open http://localhost:5500/demo.html and paste the key.

The tests are in `frontend/tests/widget.test.ts` (run with the dashboard's tests) and in
`frontend/e2e/` (the whole flow in a real browser).
