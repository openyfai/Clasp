# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
run_opcua_demo.py
=================
Connects to an OPC-UA server and feeds its data into the IndustrialSilexEngine.
"""

import argparse
import asyncio
import logging

from clasp.industrial.engine import IndustrialSilexEngine
from clasp.industrial.ingest.opcua_connector import OPCUAConnector

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("opcua_demo")

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", type=str, default="opc.tcp://localhost:4840/freeopcua/server/", help="OPC-UA Server URL")
    args = parser.parse_args()

    engine = IndustrialSilexEngine()
    await engine.initialize()
    
    connector = OPCUAConnector(engine)
    
    try:
        await connector.connect(args.url)
        await connector.discover_nodes()
        
        # This blocks forever, streaming data
        await connector.stream(interval=1.0)
    except KeyboardInterrupt:
        log.info("Stopping...")
    finally:
        await connector.disconnect()
        await engine.close()

if __name__ == "__main__":
    asyncio.run(main())
