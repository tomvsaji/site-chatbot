const form = document.querySelector("#chat-form");
const input = document.querySelector("#question");
const messages = document.querySelector("#messages");
const button = form.querySelector("button");
const clearButton = document.querySelector("#clear-chat");
const suggestions = document.querySelector("#suggestions");
const history = [];

function addMessage(text, kind, sources = []) {
  const article = document.createElement("article");
  article.className = `message ${kind}`;
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  article.appendChild(paragraph);

  if (sources.length) {
    const sourceList = document.createElement("div");
    sourceList.className = "sources";
    for (const source of sources) {
      const tag = document.createElement(source.url ? "a" : "span");
      tag.className = "source";
      tag.textContent = `[${source.number}] ${source.title || source.name}`;
      if (source.url) {
        tag.href = source.url;
        tag.target = "_blank";
        tag.rel = "noopener noreferrer";
      }
      if (source.heading) tag.title = source.heading;
      sourceList.appendChild(tag);
    }
    article.appendChild(sourceList);
  }

  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

async function sendQuestion(question) {
  if (!question) return;

  suggestions?.remove();
  addMessage(question, "user");
  const priorHistory = history.slice(-6);
  history.push({ role: "user", content: question });
  input.value = "";
  input.disabled = true;
  button.disabled = true;
  const pending = addMessage("Searching published sources…", "assistant");
  pending.classList.add("streaming", "streaming-status");
  pending.setAttribute("aria-busy", "true");
  const pendingText = pending.querySelector("p");

  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: priorHistory }),
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || "The request failed.");
    }
    if (!response.body) throw new Error("Streaming is unavailable.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const streamedClaims = [];
    let buffer = "";
    let result = null;
    let streamError = null;

    function handleEvent(block) {
      let event = "message";
      const dataLines = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) return;
      const data = JSON.parse(dataLines.join("\n"));
      if (event === "status") {
        pending.classList.add("streaming-status");
        pendingText.textContent = data.message;
      } else if (event === "claim") {
        pending.classList.remove("streaming-status");
        if (!streamedClaims.length) pendingText.replaceChildren();
        streamedClaims.push(data.text);
        const claim = document.createElement("span");
        claim.className = "streamed-claim";
        claim.textContent = data.text;
        pendingText.appendChild(claim);
        messages.scrollTop = messages.scrollHeight;
      } else if (event === "final") {
        result = data;
        pending.classList.remove("streaming", "streaming-status");
        pending.removeAttribute("aria-busy");
        pendingText.textContent = data.answer;
      } else if (event === "error") {
        streamError = new Error(data.error || "The request failed.");
      }
    }

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        handleEvent(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
      }
      if (done) break;
    }
    if (buffer.trim()) handleEvent(buffer);
    if (streamError) throw streamError;
    if (!result) throw new Error("The response stream ended unexpectedly.");

    pending.remove();
    addMessage(result.answer, "assistant", result.sources);
    history.push({ role: "assistant", content: result.answer });
  } catch (error) {
    pending.remove();
    addMessage(error.message, "error");
  } finally {
    input.disabled = false;
    button.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await sendQuestion(input.value.trim());
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll("[data-question]").forEach((suggestion) => {
  suggestion.addEventListener("click", () => sendQuestion(suggestion.dataset.question));
});

clearButton.addEventListener("click", () => {
  history.length = 0;
  messages.replaceChildren();
  addMessage("Conversation cleared. What would you like to know?", "assistant");
  input.focus();
});
