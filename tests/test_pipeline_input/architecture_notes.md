# KokertechAI Architecture Overview

## System Components

- **Controller**: Central orchestrator that manages AI provider communication and tool execution
- **Memory Vault**: SQLite-backed vector database with semantic search capabilities
- **Plugin Registry**: Dynamic tool loading with 24+ plugins for web, filesystem, and automation
- **Knowledge Graph**: Interactive neural graph visualization with drag-to-reposition

## Pipeline Flow

1. User input -> Controller -> AI Provider -> Tool Execution -> Memory Storage
2. Documents -> OCR/Text Extraction -> Chunking -> Vault Indexing -> Graph Visualization
