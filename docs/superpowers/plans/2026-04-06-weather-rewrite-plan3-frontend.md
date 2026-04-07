# Weather Arbitrage Rewrite — Plan 3: React Frontend + API

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FastAPI dashboard API and React frontend with all 9 pages. Backend serves data from PostgreSQL via SQLAlchemy. Frontend uses React + TypeScript + Tailwind + shadcn/ui + Recharts + TanStack Query.

**Architecture:** FastAPI endpoints under `/api/*`. React SPA served from `frontend/dist/` via StaticFiles in production, Vite dev server with proxy in development.

**Tech Stack:** FastAPI, Pydantic, React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui, Recharts, TanStack Query v5, TanStack Table v8, React Hook Form + Zod

---

## Tasks

1. FastAPI dashboard endpoints (11 endpoints)
2. React project scaffold (Vite + TS + Tailwind + shadcn/ui)
3. API client hooks + layout shell
4. Overview + Opportunities pages
5. Positions + Trade History pages
6. Weather Monitor + Edge Calibration pages
7. Config Editor + City Mapping pages
8. System Logs page + production build integration
