# Market Research

A Python-based market research platform for importing, storing, and analyzing financial market data.

## Current functionality

The application currently provides:

* Web interface for importing CSV market data
* CSV file upload through the browser
* Instrument storage in PostgreSQL
* Market data storage model
* Database migrations with Alembic
* FastAPI backend
* Static JavaScript frontend

## Development approach

The project is developed incrementally:

```text
Need → implement → test → real problem → solve → commit → continue
```

The goal is to build the system step by step based on real requirements rather than adding unnecessary complexity in advance.
