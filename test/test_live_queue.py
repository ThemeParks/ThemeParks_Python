"""
    Theme Parks Wiki V1 API

    api.themeparks.wiki  # noqa: E501

    The version of the OpenAPI document: 1.0.0-alpha
    Generated by: https://openapi-generator.tech
"""


import sys
import unittest

import openapi_client
from openapi_client.model.live_queue_boardinggroup import LiveQueueBOARDINGGROUP
from openapi_client.model.live_queue_paidreturntime import LiveQueuePAIDRETURNTIME
from openapi_client.model.live_queue_returntime import LiveQueueRETURNTIME
from openapi_client.model.live_queue_standby import LiveQueueSTANDBY
globals()['LiveQueueBOARDINGGROUP'] = LiveQueueBOARDINGGROUP
globals()['LiveQueuePAIDRETURNTIME'] = LiveQueuePAIDRETURNTIME
globals()['LiveQueueRETURNTIME'] = LiveQueueRETURNTIME
globals()['LiveQueueSTANDBY'] = LiveQueueSTANDBY
from openapi_client.model.live_queue import LiveQueue


class TestLiveQueue(unittest.TestCase):
    """LiveQueue unit test stubs"""

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def testLiveQueue(self):
        """Test LiveQueue"""
        # FIXME: construct object with mandatory attributes with example values
        # model = LiveQueue()  # noqa: E501
        pass


if __name__ == '__main__':
    unittest.main()