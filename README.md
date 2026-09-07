# LifeNest-AI

LifeNest AI is an AI-powered financial risk advisor for first-time Malaysian home buyers that computes a buyer's true affordability before they sign.

## Core Inputs

Users can provide their financial data in either of two ways:

- upload a photo of their payslip and EPF statement
- manually input the same financial figures

## Dual-Engine Architecture

LifeNest AI uses a dual-engine architecture to separate reasoning from calculation:

1. **Gemini 1.5 Flash** acts as the cognitive, multilingual parser and risk advisor.
2. **A deterministic Python mathematical engine** performs the actual financial calculations.
