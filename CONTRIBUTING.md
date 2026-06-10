# Contributing to Clasp

Thank you for your interest in contributing to **Clasp**! We welcome bug reports, feature requests, and code contributions from the community to help us build the best autonomous industrial causal engine.

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Development Setup

To set up a local development environment:

1. Clone the repository and navigate to the project root.
2. Install the package in editable mode with development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
3. Run the verification tests to ensure your environment is configured correctly:
   ```bash
   pytest
   ```

## Code Style & Linting

We adhere to standard **PEP-8 guidelines** for all Python code. 
- Please ensure your code is cleanly formatted.
- Use meaningful variable names, add docstrings to classes and complex functions, and include inline comments where necessary.
- We recommend using formatting and linting tools (like `black`, `flake8`, or `ruff`) before committing your changes.

## Pull Request Process

When you are ready to submit your changes, please open a Pull Request (PR).
To ensure a smooth review process:
1. **Green Tests:** Your branch must pass all unit tests (`pytest`) before it can be merged. If you add new functionality, please include the corresponding tests.
2. **Clear Description:** Provide a clear and comprehensive description of the changes in your PR. If your PR resolves an open issue, reference it (e.g., "Fixes #123").
3. **Review:** A maintainer will review your PR and provide feedback. Please be prepared to iterate on your changes.

## Contributor License Agreement (CLA)

By submitting a contribution (including pull requests, code, or documentation) to this repository, you agree to grant openyfai (YF) a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable copyright license to reproduce, prepare derivative works of, publicly display, sublicense, and distribute your contributions. This ensures that openyfai can continue to distribute the core project under source-available terms while packaging advanced enterprise capabilities for commercial deployment.
