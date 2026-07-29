# Dreamer: The Personal Agent OS That Wants To Run Your Day

Source: https://tomvsaji.com/blogs/dreamer-personal-agent-os

Published: March 22, 2026

Reading time: 8 min read

A practical look at Dreamer, Sidekick, and why the platform is positioning itself as an operating system for personal AI agents.

Over the last year, AI assistants have gone from fun demos to tools we actually rely on. But most of them still live in a single chat box. Dreamer takes a different approach: it is building an operating system for **personal agents** that can live with you across your entire day, not just answer questions in a tab.

## What Is Dreamer?

Dreamer is a cloud platform where your main interface is a personal agent called **Sidekick**, surrounded by a growing ecosystem of other agents and tools.
You talk to Sidekick in natural language, and it in turn uses other agents such as calendar agents, research agents, coding agents, and reading agents to get work done on your behalf.
Under the hood, these agents are real software running in secure, isolated VMs, with access to tools, data sources, and each other through a common OS layer.

The Dreamer team describes it as “an operating system for AI agents and agentic apps,” taking lessons from Android, ChromeOS, and other platforms they previously worked on.
That OS metaphor is not just branding: it has primitives for agents, tools, permissions, logging, a database, and serverless functions, so you are effectively installing and composing intelligent apps, not just prompts.

## How Dreamer Helps With Everyday Life

Dreamer’s pitch is simple: instead of juggling dozens of apps, filters, and automations, you describe outcomes to Sidekick, and it orchestrates agents and tools to make them happen.

Here are a few ways that can play out in day-to-day life.

### 1. Your personal operations chief

You might say: `Get me ready for tomorrow.`
Sidekick can pull from calendar agents, email agents, and reading agents to build a single briefing with your meetings, relevant docs, and articles you saved that relate to the topics on your schedule.
Instead of bouncing between inbox, calendar, and notes, you get one coherent snapshot shaped around what you actually need to know.

Another example is: `Clean up my inbox and surface what matters.`
A mail agent can classify emails, summarize long threads, and highlight messages that need action, while a calendar agent suggests follow-up slots and drafts responses where appropriate.

### 2. Turning chores into background processes

Because agents can call each other, you can chain workflows without writing scripts.

A simple scenario the team highlights:

- You say: `Plan dinner for tonight and add the ingredients to my grocery list.`
- Sidekick uses a Recipe agent to pick something aligned with your preferences.
- It extracts ingredients and passes them to a Grocery List agent, which formats and stores them wherever you track shopping.

What you experience is a conversational request and a ready-to-shop list. What is actually happening is multiple agents talking through the OS.

### 3. A smarter reading and research flow

Imagine saying: `Whenever I star an article, summarize it and add key points to my second brain.`
In Dreamer, Sidekick can wire up a Personal News agent that discovers or ingests articles, along with a Read Later or Notes agent that stores content and structured summaries.

Because agents expose functions, the News agent can send an article directly into the Read Later agent without you copy-pasting anything.
You are effectively building custom pipelines for information digestion using natural language, not automation GUIs.

### 4. Help for non-technical and technical users

If you are not technical, you can stay in Sidekick and the agent gallery: discover agents, install them, and describe workflows you want, such as `When X happens, do Y and Z for me`.
If you are technical, Dreamer offers an SDK, CLI, logging, a database, and serverless functions so you can ship full-blown agentic apps that run arbitrary code in their VMs.

Crucially, the SDK is designed so that Sidekick and other coding agents can manipulate it too, meaning agents can help build and improve other agents.
That is where the `agent that builds agents` idea comes from, and it is a big part of why Dreamer feels different from typical agent builders.

## What Makes Dreamer Special?

Lots of products call themselves an agent OS or AI workspace. Dreamer stands out because it is opinionated about being an OS for intelligent software, not just a wrapper around an LLM.

### 1. Sidekick as the system agent

At the center of Dreamer is Sidekick, a system-level agent that knows about all your other agents, tools, and data, and mediates how they work together.
You can name it, shape its personality, and treat it as your long-term collaborator that understands your preferences and workflows.

Sidekick is also important for safety and control: other agents do not just run wild; they go through Sidekick and the OS permission model when they want to use tools or access data.

### 2. Composable agents with real capabilities

Every agent in Dreamer can expose **functions** to the rest of the system, which means any capability like `summarize this PDF`, `fetch from this API`, or `update this doc` can be reused by other agents or by Sidekick.
This turns agents into building blocks you can wire together, instead of isolated chatbots that force you to glue everything with manual steps.

The Dreamer team gives examples where content discovered by one agent can be passed directly into another agent built by someone else.
The OS provides the conventions and runtime that make this interoperability reliable.

### 3. A clear tools and permissions model

Dreamer treats tools as first-class: each tool is an MCP server plus optional skills, branding, and metadata that show up in the gallery.
Tools sit inside the permissions model, so the OS knows exactly which tools an agent might use, and you can inspect and approve those when you install someone else’s agent.

This is a meaningful shift from opaque plugin systems in some assistants. You do not just click `enable everything`; you see what is in play, who built it, and what each agent can do with it.

### 4. Full-stack, software 3.0 foundation

Rather than limiting you to no-code blocks, Dreamer lets builders deploy agents that:

- Run arbitrary deterministic code where that is best
- Call prompts into state-of-the-art LLMs
- Invoke sub-agents and Sidekick tasks that can manipulate the whole system

Agents run in secure, isolated VMs with physical data separation per user, giving a more robust foundation for serious personal software.
The system is designed so that everything you build is intelligence-native by default, which the team frames as `software 3.0`.

### 5. Community and ecosystem, not just a product

Dreamer is intentionally cultivating a community of builders: an early alpha group, builders-in-residence, and even cash prizes for useful agents and tools.
The goal is to make Dreamer feel like an app store where every app can talk to every other app, and where both hobbyists and professionals can publish and monetize their creations.

That ecosystem focus is key to the OS ambition. No single company can anticipate every niche workflow; you need thousands of people building agents that solve their own problems and then sharing them.

## How You Actually Use Dreamer Today

Dreamer is currently in beta with a waitlist, open to both technical and non-technical early adopters.
You access it primarily via a web app that works on modern browsers on Windows, macOS, and Linux, with Sidekick and agents available across devices and surfaces.

Once you are in, a typical journey looks like:

- Naming and personalizing your Sidekick
- Installing a few starter agents from the gallery, such as email, reading, coding, and planning agents
- Asking Sidekick to tie them into workflows that match your real life, such as `prepare me for tomorrow`, `optimize my reading`, or `help me with recruiting`

If you are a builder, you can drop down into the SDK and CLI to create custom agents, share them with the community, and eventually monetize them as the platform matures.

## Why Dreamer Matters

The Dreamer team is betting that 2026 is the year of the **personal agent**, and that the right abstraction is not `chat with a model`, but `live with a Sidekick that coordinates many agents on an OS built for them`.
If they are right, the way we use computers shifts: you spend less time clicking and configuring, and more time stating goals while a network of agents quietly handles the glue work.

For people overwhelmed by tools, tabs, and to-dos, that is a compelling vision: not just smarter apps, but a smarter environment that is actually shaped around you.

:::details References
- [David Singleton on Latent Space](https://www.latent.space/p/dreamer)
- [Dreamer launch post](https://blog.singleton.io/posts/2026-02-17-introducing-dreamer/)
- [Sidekick tour](https://www.youtube.com/watch?v=k07CJ8afVgQ)
- [StartupHub overview](https://www.startuphub.ai/ai-news/artificial-intelligence/2026/dreamer-building-ai-agents-for-everyone)
- [Dreamer community strategy](https://blog.singleton.io/posts/2026-03-17-community-is-a-product-decision/)
:::
