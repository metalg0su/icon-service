# -*- coding: utf-8 -*-
#
# Copyright 2019 ICON Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import hashlib
import unittest
from typing import List

from iconservice.base.address import Address
from iconservice.base.block import Block
from iconservice.icon_constant import PREP_MAIN_PREPS, PREP_MAIN_AND_SUB_PREPS
from iconservice.icon_constant import Revision
from iconservice.iconscore.icon_score_base2 import PRepInfo, get_main_prep_info, get_sub_prep_info
from iconservice.iconscore.icon_score_base2 import ScoreApiStepRatio
from iconservice.iconscore.icon_score_base2 import _create_address_with_key, _recover_key
from iconservice.iconscore.icon_score_base2 import create_address_with_key, recover_key
from iconservice.iconscore.icon_score_base2 import sha3_256, sha_256, json_dumps, json_loads
from iconservice.iconscore.icon_score_context import ContextContainer
from iconservice.iconscore.icon_score_context import IconScoreContext, IconScoreContextType, IconScoreContextFactory
from iconservice.iconscore.icon_score_step import IconScoreStepCounterFactory, StepType
from iconservice.prep import PRepEngine
from iconservice.prep.data import PRep, Term, PRepContainer
from iconservice.utils import ContextEngine
from tests import create_address


def create_msg_hash(tx: dict, excluded_keys: tuple) -> bytes:
    keys = [key for key in tx if key not in excluded_keys]
    keys.sort()

    msg = 'icx_sendTransaction'
    for key in keys:
        value: str = tx[key]
        msg += f'.{key}.{value}'

    return hashlib.sha3_256(msg.encode('utf-8')).digest()


class TestIconScoreApi(unittest.TestCase):
    def setUp(self):
        # The transaction in block 1000 of TestNet
        self.tx_v2 = {
            'from': 'hxdbc9f726ad776d9a43d5bad387eff01325178fa3',
            'to': 'hx0fb148785e4a5d77d16429c7ed2edae715a4453a',
            'value': '0x324e964b3eca80000',
            'fee': '0x2386f26fc10000',
            'timestamp': '1519709385120909',
            'tx_hash': '1257b9ea76e716b145463f0350f534f973399898a18a50d391e7d2815e72c950',
            'signature': 'WiRTA/tUNGVByc8fsZ7+U9BSDX4BcBuv2OpAuOLLbzUiCcovLPDuFE+PBaT8ovmz5wg+Bjr7rmKiu7Rl8v0DUQE=',
        }

        # The transaction in block 100000 of MainNet
        self.tx_v3 = {
            'version': '0x3',
            'nid': '0x1',
            'from': 'hx522bff55a62e0c75a1b51855b0802cfec6a92e84',
            'to': 'hx11de4e28be4845de3ea392fd8d758655bf766ca7',
            'value': '0x71afd498d0000',
            'stepLimit': '0xf4240',
            'timestamp': '0x57a4e5556cc03',
            'signature': 'fcEMXqEGlqEivXXr7YtD/F1RXgxSXF+R4gVrGKxT1zxi3HukX4NzkSl9/Es1G+nyZx+kviTAtQFUrA+/T0NrfAA=',
            'txHash': '6c71ac77b2d130a1f81d234e814974e85cabb0a3ec462c66ff3f820502d0ded2'
        }

        self.step_costs = {
            StepType.DEFAULT: 0,
            StepType.CONTRACT_CALL: 25_000,
            StepType.CONTRACT_CREATE: 1_000_000_000,
            StepType.CONTRACT_UPDATE: 1_600_000_000,
            StepType.CONTRACT_DESTRUCT: -70000,
            StepType.CONTRACT_SET: 30_000,
            StepType.GET: 0,
            StepType.SET: 320,
            StepType.REPLACE: 80,
            StepType.DELETE: -240,
            StepType.INPUT: 200,
            StepType.EVENT_LOG: 100,
            StepType.API_CALL: 10_000
        }
        self.step_limit = 1_000_000_000

        self._prep_engine = PRepEngine()

        self.context = self._create_context()
        ContextContainer._push_context(self.context)

    def _create_context(self):
        IconScoreContext.engine = \
            ContextEngine(deploy=None, fee=None, icx=None, iiss=None, prep=self._prep_engine, issue=None)

        block = Block(block_height=1, block_hash=b"1" * 40, prev_hash=b"0" * 40, timestamp=0)
        step_counter_factory = self._create_step_counter_factory()

        context_factory = IconScoreContextFactory(step_counter_factory)

        context = context_factory.create(IconScoreContextType.INVOKE, block)
        context.revision = Revision.THREE.value
        context.step_counter.reset(self.step_limit)

        return context

    def _create_step_counter_factory(self) -> 'IconScoreStepCounterFactory':
        factory = IconScoreStepCounterFactory()
        factory.set_step_properties(
            step_price=10 ** 10, step_costs=self.step_costs, max_step_limits=None)
        factory.set_max_step_limit(IconScoreContextType.INVOKE, 2_500_000_000)

        return factory

    def _calc_step_cost(self, ratio: ScoreApiStepRatio) -> int:
        step_cost: int = self.step_costs[StepType.API_CALL] * ratio // ScoreApiStepRatio.SHA3_256
        self.assertTrue(isinstance(step_cost, int))
        self.assertTrue(step_cost > 0)

        return step_cost

    def tearDown(self):
        ContextContainer._pop_context()
        assert ContextContainer._get_context_stack_size() == 0

    def test_recover_key_v2_and_create_address_with_key(self):
        signature: bytes = base64.b64decode(self.tx_v2['signature'])
        self.assertIsInstance(signature, bytes)
        self.assertTrue(len(signature) > 0)

        msg_hash: bytes = create_msg_hash(self.tx_v2, ('tx_hash', 'signature'))
        self.assertEqual(msg_hash, bytes.fromhex(self.tx_v2['tx_hash']))

        uncompressed_public_key: bytes = _recover_key(msg_hash, signature, compressed=False)
        self.assertIsInstance(uncompressed_public_key, bytes)
        self.assertEqual(65, len(uncompressed_public_key))
        self.assertEqual(0x04, uncompressed_public_key[0])

        address: Address = _create_address_with_key(uncompressed_public_key)
        self.assertEqual(self.tx_v2['from'], str(address))

        compressed_public_key: bytes = _recover_key(msg_hash, signature, compressed=True)
        self.assertIsInstance(compressed_public_key, bytes)
        self.assertEqual(33, len(compressed_public_key))
        self.assertIn(compressed_public_key[0], (0x02, 0x03))

        address: Address = _create_address_with_key(compressed_public_key)
        self.assertEqual(self.tx_v2['from'], str(address))

    def test_recover_key_v3_and_create_address_with_key(self):
        signature: bytes = base64.b64decode(self.tx_v3['signature'])
        self.assertIsInstance(signature, bytes)
        self.assertTrue(len(signature) > 0)

        msg_hash: bytes = create_msg_hash(self.tx_v3, ('txHash', 'signature'))
        self.assertEqual(msg_hash, bytes.fromhex(self.tx_v3['txHash']))

        uncompressed_public_key: bytes = _recover_key(msg_hash, signature, compressed=False)
        self.assertIsInstance(uncompressed_public_key, bytes)
        self.assertEqual(65, len(uncompressed_public_key))
        self.assertEqual(0x04, uncompressed_public_key[0])

        address: Address = _create_address_with_key(uncompressed_public_key)
        self.assertEqual(self.tx_v3['from'], str(address))

        compressed_public_key: bytes = _recover_key(msg_hash, signature, compressed=True)
        self.assertIsInstance(compressed_public_key, bytes)
        self.assertEqual(33, len(compressed_public_key))
        self.assertIn(compressed_public_key[0], (0x02, 0x03))

        address: Address = _create_address_with_key(compressed_public_key)
        self.assertEqual(self.tx_v3['from'], str(address))

    def test_recover_key_step_with_tx_v3(self):
        step_cost: int = self._calc_step_cost(ScoreApiStepRatio.RECOVER_KEY)

        signature: bytes = base64.b64decode(self.tx_v3['signature'])
        self.assertIsInstance(signature, bytes)
        self.assertTrue(len(signature) > 0)

        msg_hash: bytes = create_msg_hash(self.tx_v3, ('txHash', 'signature'))
        self.assertEqual(msg_hash, bytes.fromhex(self.tx_v3['txHash']))

        uncompressed_public_key: bytes = recover_key(msg_hash, signature, compressed=False)
        self.assertIsInstance(uncompressed_public_key, bytes)
        self.assertEqual(65, len(uncompressed_public_key))
        self.assertEqual(0x04, uncompressed_public_key[0])

        step_used: int = self.context.step_counter.step_used
        self.assertEqual(step_cost, step_used)

        self.context.step_counter.reset(self.step_limit)

        compressed_public_key: bytes = recover_key(msg_hash, signature, compressed=True)
        self.assertIsInstance(compressed_public_key, bytes)
        self.assertEqual(33, len(compressed_public_key))
        self.assertIn(compressed_public_key[0], (0x02, 0x03))

        step_used: int = self.context.step_counter.step_used
        self.assertEqual(step_cost, step_used)

    def test_create_address_with_key_step_with_tx_v3(self):
        uncompressed_step_cost: int = self._calc_step_cost(ScoreApiStepRatio.CREATE_ADDRESS_WITH_UNCOMPRESSED_KEY)
        compressed_step_cost: int = self._calc_step_cost(ScoreApiStepRatio.CREATE_ADDRESS_WITH_COMPRESSED_KEY)
        self.assertTrue(uncompressed_step_cost != compressed_step_cost)

        signature: bytes = base64.b64decode(self.tx_v3['signature'])
        self.assertIsInstance(signature, bytes)
        self.assertTrue(len(signature) > 0)

        msg_hash: bytes = create_msg_hash(self.tx_v3, ('txHash', 'signature'))
        self.assertEqual(msg_hash, bytes.fromhex(self.tx_v3['txHash']))

        uncompressed_public_key: bytes = recover_key(msg_hash, signature, compressed=False)
        self.assertIsInstance(uncompressed_public_key, bytes)
        self.assertEqual(65, len(uncompressed_public_key))
        self.assertEqual(0x04, uncompressed_public_key[0])

        self.context.step_counter.reset(self.step_limit)

        address: Address = create_address_with_key(uncompressed_public_key)
        self.assertEqual(self.tx_v3['from'], str(address))

        step_used: int = self.context.step_counter.step_used
        self.assertEqual(uncompressed_step_cost, step_used)

        compressed_public_key: bytes = recover_key(msg_hash, signature, compressed=True)
        self.assertIsInstance(compressed_public_key, bytes)
        self.assertEqual(33, len(compressed_public_key))
        self.assertIn(compressed_public_key[0], (0x02, 0x03))

        self.context.step_counter.reset(self.step_limit)

        address: Address = create_address_with_key(compressed_public_key)
        self.assertEqual(self.tx_v3['from'], str(address))

        step_used: int = self.context.step_counter.step_used
        self.assertEqual(compressed_step_cost, step_used)

    def test_sha3_256(self):
        step_cost: int = self._calc_step_cost(ScoreApiStepRatio.SHA3_256)

        for i in range(0, 512):
            chunks = i // 32
            if i % 32 > 0:
                chunks += 1

            data: bytes = b'\x00' * i
            hash_value: bytes = sha3_256(data)
            assert hash_value == hashlib.sha3_256(data).digest()

            expected_step: int = step_cost + step_cost * chunks // 10
            step_used: int = self.context.step_counter.step_used
            assert step_used == expected_step

            self.context.step_counter.reset(self.step_limit)

    def test_sha_256(self):
        step_cost: int = self._calc_step_cost(ScoreApiStepRatio.SHA_256)

        for i in range(0, 512):
            chunks = i // 32
            if i % 32 > 0:
                chunks += 1

            data: bytes = b'\x00' * i
            hash_value: bytes = sha_256(data)
            assert hash_value == hashlib.sha256(data).digest()

            expected_step: int = step_cost + step_cost * chunks // 10
            step_used: int = self.context.step_counter.step_used
            assert step_used == expected_step

            self.context.step_counter.reset(self.step_limit)

    def test_json_dumps_step(self):
        step_cost: int = self._calc_step_cost(ScoreApiStepRatio.JSON_DUMPS)

        for i in range(1, 100):
            obj = {}

            for j in range(i):
                obj[f'key{j}'] = f'value{j}'

            text: str = json_dumps(obj)

            expected_step: int = step_cost + step_cost * len(text.encode('utf-8')) // 100
            step_used: int = self.context.step_counter.step_used
            self.assertEqual(expected_step, step_used)

            obj2: dict = json_loads(text)
            self.assertEqual(obj, obj2)

            self.context.step_counter.reset(self.step_limit)

    def test_json_loads_step(self):
        step_cost: int = self._calc_step_cost(ScoreApiStepRatio.JSON_LOADS)

        for i in range(1, 100):
            obj = {}

            for j in range(i):
                obj[f'key{j}'] = f'value{j}'

            text: str = json_dumps(obj)

            self.context.step_counter.reset(self.step_limit)

            obj2: dict = json_loads(text)
            self.assertEqual(obj, obj2)

            expected_step: int = step_cost + step_cost * len(text.encode('utf-8')) // 100
            step_used: int = self.context.step_counter.step_used
            self.assertEqual(expected_step, step_used)

    def test_get_prep_info(self):
        main_prep_list, end_block_height = get_main_prep_info()
        self.assertEqual([], main_prep_list)
        self.assertEqual(-1, end_block_height)

        sub_prep_list, end_block_height = get_sub_prep_info()
        self.assertEqual([], main_prep_list)
        self.assertEqual(-1, end_block_height)

        # term._preps to contexts
        prep_infos: List['PRepInfo'] = []
        preps: 'PRepContainer' = PRepContainer()
        for i in range(PREP_MAIN_AND_SUB_PREPS):
            delegated: int = PREP_MAIN_AND_SUB_PREPS - i
            prep_info = PRepInfo(address=create_address(), delegated=delegated, name=f"prep{i}")
            prep_infos.append(prep_info)

            prep = PRep(address=prep_info.address, delegated=prep_info.delegated, name=prep_info.name)
            preps.add(prep)

        term = Term(sequence=0,
                    start_block_height=61,
                    period=40,
                    irep=50_000,
                    total_supply=1_000_000_000,
                    total_delegated=1_000_000_000)
        term.set_preps(preps, PREP_MAIN_PREPS, PREP_MAIN_AND_SUB_PREPS)
        term.freeze()

        self.context.engine.prep.term = term
        self.context._term = term.copy()
        self.context._preps = preps.copy(mutable=True)

        # check main P-Rep info
        main_prep_list, end_block_height = get_main_prep_info()
        for i, prep_info in enumerate(main_prep_list):
            prep = preps.get_by_address(prep_info.address)
            self.assertEqual(prep_infos[i].address, prep.address)
            self.assertEqual(prep_infos[i].delegated, prep.delegated)
            self.assertEqual(prep_infos[i].name, prep.name)

            self.assertEqual(prep.address, prep_info.address)
            self.assertEqual(prep.delegated, prep_info.delegated)
            self.assertEqual(prep.name, prep_info.name)

        self.assertEqual(self.context.engine.prep.term.end_block_height, end_block_height)

        # check sub P-Rep info
        for i, prep_info in enumerate(sub_prep_list):
            j = i + PREP_MAIN_PREPS
            prep = preps.get_by_address(prep_info.address)
            self.assertEqual(prep_infos[j].address, prep.address)
            self.assertEqual(prep_infos[j].delegated, prep.delegated)
            self.assertEqual(prep_infos[j].name, prep.name)

            self.assertEqual(prep.address, prep_info.address)
            self.assertEqual(prep.delegated, prep_info.delegated)
            self.assertEqual(prep.name, prep_info.name)

        # check end block height
        self.assertEqual(self.context.engine.prep.term.end_block_height, end_block_height)


if __name__ == '__main__':
    unittest.main()
