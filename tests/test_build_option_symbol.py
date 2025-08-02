import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import optionstrader

def test_build_option_symbol_basic():
    sym = optionstrader.build_option_symbol('btc', '114000', 'put', '7/6/25', 'usdt')
    assert sym == 'BTC-7JUN25-114000-P-USDT'

def test_build_option_symbol_call():
    sym = optionstrader.build_option_symbol('eth', '2500', 'CALL', '12/11/24', 'usdc')
    assert sym == 'ETH-12NOV24-2500-C-USDC'
