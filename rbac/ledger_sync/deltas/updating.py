# Copyright 2018 Contributors to Hyperledger Sawtooth
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# -----------------------------------------------------------------------------

import sys
import logging
from rethinkdb import r

from rbac.common import addresser
from rbac.common.addresser import AddressSpace
from rbac.common.util import bytes_from_hex


LOGGER = logging.getLogger(__name__)

TABLE_NAMES = {
    AddressSpace.USER: "users",
    AddressSpace.PROPOSALS: "proposals",
    AddressSpace.SYSADMIN_ATTRIBUTES: "roles",
    AddressSpace.SYSADMIN_MEMBERS: "role_members",
    AddressSpace.SYSADMIN_OWNERS: "role_owners",
    AddressSpace.SYSADMIN_ADMINS: "role_admins",
    AddressSpace.ROLES_ATTRIBUTES: "roles",
    AddressSpace.ROLES_MEMBERS: "role_members",
    AddressSpace.ROLES_OWNERS: "role_owners",
    AddressSpace.ROLES_ADMINS: "role_admins",
    AddressSpace.ROLES_TASKS: "role_tasks",
    AddressSpace.TASKS_ATTRIBUTES: "tasks",
    AddressSpace.TASKS_OWNERS: "task_owners",
    AddressSpace.TASKS_ADMINS: "task_admins",
}

FILTER_KEYS = {
    AddressSpace.USER: ["user_id"],
    AddressSpace.PROPOSALS: ["proposal_id"],
    AddressSpace.SYSADMIN_ATTRIBUTES: ["role_id"],
    AddressSpace.SYSADMIN_MEMBERS: ["role_id", "suffix"],
    AddressSpace.SYSADMIN_OWNERS: ["role_id", "suffix"],
    AddressSpace.SYSADMIN_ADMINS: ["role_id", "suffix"],
    AddressSpace.ROLES_ATTRIBUTES: ["role_id"],
    AddressSpace.ROLES_MEMBERS: ["role_id", "suffix"],
    AddressSpace.ROLES_OWNERS: ["role_id", "suffix"],
    AddressSpace.ROLES_ADMINS: ["role_id", "suffix"],
    AddressSpace.ROLES_TASKS: ["role_id", "suffix"],
    AddressSpace.TASKS_ATTRIBUTES: ["task_id"],
    AddressSpace.TASKS_OWNERS: ["task_id", "suffix"],
    AddressSpace.TASKS_ADMINS: ["task_id", "suffix"],
}

ADD_SUFFIX = {
    AddressSpace.SYSADMIN_MEMBERS: True,
    AddressSpace.SYSADMIN_OWNERS: True,
    AddressSpace.SYSADMIN_ADMINS: True,
    AddressSpace.ROLES_MEMBERS: True,
    AddressSpace.ROLES_OWNERS: True,
    AddressSpace.ROLES_ADMINS: True,
    AddressSpace.ROLES_TASKS: True,
    AddressSpace.TASKS_OWNERS: True,
    AddressSpace.TASKS_ADMINS: True,
}


def get_updater(database, block_num):
    """Returns an updater function, which can be used to update the database
    appropriately for a particular address/data combo.
    """
    return lambda adr, rsc: _update(database, block_num, adr, rsc)


def _update_state(database, block_num, address, resource):
    try:
        # update state table
        state = dict(resource)
        address_parts = addresser.parse(address)
        address_binary = bytes_from_hex(address)
        key = address_binary
        keys = {"address": address_binary}
        data = {
            "block_updated": int(block_num),
            "updated_at": r.now(),
            "object_type": address_parts.object_type.value,
            "object_id": bytes_from_hex(address_parts.object_id),
            "related_type": address_parts.related_type.value,
            "relationship_type": address_parts.relationship_type.value,
            "related_id": bytes_from_hex(address_parts.related_id),
            "data": resource,
        }
        table_query = database.get_table("state")
        query = table_query.get(key).replace(
            lambda doc: r.branch(
                # pylint: disable=singleton-comparison
                (doc == None),  # noqa
                r.expr(data).merge(
                    {
                        "address": key,
                        "block_created": int(block_num),
                        "created_at": r.now(),
                    }
                ),
                doc.merge(data),
            )
        )
        result = database.run_query(query)
        if not result["inserted"] == 1 and not result["replaced"]:
            LOGGER.warning("error updating state table:\n%s\n%s", result, query)

        key = [address_binary, int(block_num)]
        data["address"] = key
        if result["inserted"] == 1:
            data["block_created"] = int(block_num)
            data["created_at"] = r.now()
        elif result["replaced"] == 1:
            LOGGER.warning(result)

        table_query = database.get_table("state_history")
        query = table_query.get(key).replace(data)
        result = database.run_query(query)
        if not result["inserted"] == 1 and not result["replaced"]:
            LOGGER.warning("error updating state_history table:\n%s\n%s", result, query)

    except Exception as err:  # pylint: disable=broad-except
        LOGGER.warning("update_state %s error:", type(err))
        LOGGER.warning(err)


def _update(database, block_num, address, resource):
    data_type = addresser.address_is(address)

    _update_state(database, block_num, address, resource)

    resource["start_block_num"] = block_num
    resource["end_block_num"] = str(sys.maxsize)

    if ADD_SUFFIX.get(data_type, False):
        resource["suffix"] = int(address[-2:], 16)

    try:
        table_query = database.get_table(TABLE_NAMES[data_type])
        update_filter = {k: resource[k] for k in FILTER_KEYS[data_type]}
    except KeyError:
        raise TypeError("Unknown data type: {}".format(data_type))

    update_filter["end_block_num"] = str(sys.maxsize)

    query = (
        table_query.filter(update_filter)
        .update({"end_block_num": block_num})
        .merge(table_query.insert(resource).without("replaced"))
    )

    return database.run_query(query)
