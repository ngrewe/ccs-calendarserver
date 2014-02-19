# -*- test-case-name: txdav.common.datastore.work.test.test_revision_cleanup -*-
##
# Copyright (c) 2013-2014 Apple Inc. All rights reserved.
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
##

"""
Remove orphaned and old inbox items, and inbox items references old events
"""

from twext.enterprise.dal.record import fromTable
from twext.enterprise.dal.syntax import Delete, Select
from twext.enterprise.queue import WorkItem
from twext.python.log import Logger
from twisted.internet.defer import inlineCallbacks, returnValue
from twistedcaldav.config import config
from txdav.common.datastore.sql_tables import schema, _HOME_STATUS_NORMAL
import datetime

log = Logger()


class InboxCleanupWork(WorkItem,
    fromTable(schema.INBOX_CLEANUP_WORK)):

    group = "clean_inboxes"

    @classmethod
    @inlineCallbacks
    def _schedule(cls, txn, seconds):
        notBefore = datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)
        log.debug("Scheduling clean inboxes work: {}".format(notBefore,))
        wp = yield txn.enqueue(cls, notBefore=notBefore)
        returnValue(wp)


    @inlineCallbacks
    def doWork(self):

        # Delete all other work items
        yield Delete(From=self.table, Where=None).on(self.transaction)

        # enumerate provisioned normal calendar homes
        ch = schema.CALENDAR_HOME
        homeRows = yield Select(
            [ch.RESOURCE_ID],
            From=ch,
            Where=ch.STATUS == _HOME_STATUS_NORMAL,
        ).on(self.transaction)

        for homeRow in homeRows:
            yield CleanupOneInboxWork._schedule(self.transaction, homeID=homeRow[0], seconds=0)

        # Schedule next check
        yield InboxCleanupWork._schedule(
            self.transaction,
            float(config.InboxCleanupPeriodDays) * 24 * 60 * 60
        )



class CleanupOneInboxWork(WorkItem,
    fromTable(schema.CLEANUP_ONE_INBOX_WORK)):

    group = property(lambda self: "clean_inbox_in_homeid_{}".format(self.homeID))

    @classmethod
    @inlineCallbacks
    def _schedule(cls, txn, homeID, seconds):
        notBefore = datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)
        log.debug("Scheduling Inbox cleanup work: {notBefore} in home id: {homeID}".format(
            notBefore=notBefore, homeID=homeID))
        wp = yield txn.enqueue(cls, notBefore=notBefore, homeID=homeID)
        returnValue(wp)


    @inlineCallbacks
    def doWork(self):

        # Delete all other work items for this group (for this home ID)
        yield Delete(From=self.table, Where=None).on(self.transaction)

        # get orphan names
        orphanNames = set((
            yield self.transaction.orphanedInboxItemsInHomeID(self.homeID)
        ))
        if orphanNames:
            home = yield self.transaction.calendarHomeWithResourceID(self.homeID)
            log.info("Inbox cleanup work in home: {homeUID}, deleting orphaned items: {orphanNames}".format(
                homeID=self.uid(), orphanNames=orphanNames))

        # get old item names
        if float(config.InboxItemLifetimeDays) >= 0: # use -1 to disable; 0 is test case
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=float(config.InboxItemLifetimeDays))
            oldItemNames = set((
                yield self.transaction.listInboxItemsInHomeCreatedBefore(self.homeID, cutoff)
            ))
            newDeleters = oldItemNames - orphanNames
            if newDeleters:
                home = yield self.transaction.calendarHomeWithResourceID(self.homeID)
                log.info("Inbox cleanup work in home: {homeUID}, deleting old items: {newDeleters}".format(
                    homeUID=home.uid(), newDeleters=newDeleters))
        else:
            oldItemNames = set()

        # get item name for old events
        if float(config.InboxItemLifetimePastEventEndDays) >= 0: # use -1 to disable; 0 is test case
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=float(config.InboxItemLifetimePastEventEndDays))
            itemNamesForOldEvents = set((
                yield self.transaction.listInboxItemsInHomeForEventsBefore(self.homeID, cutoff)
            ))
            newDeleters = itemNamesForOldEvents - oldItemNames - orphanNames
            if newDeleters:
                home = yield self.transaction.calendarHomeWithResourceID(self.homeID)
                log.info("Inbox cleanup work in home: {homeUID}, deleting items for old events: {newDeleters}".format(
                    homeUID=home.uid(), newDeleters=newDeleters))
        else:
            itemNamesForOldEvents = set()

        itemNamesToDelete = orphanNames | itemNamesForOldEvents | oldItemNames
        if itemNamesToDelete:
            inbox = yield home.childWithName("inbox")
            for item in (yield inbox.objectResourcesWithNames(itemNamesToDelete)):
                yield item.remove()



@inlineCallbacks
def scheduleFirstInboxCleanup(store, seconds):
    txn = store.newTransaction()
    wp = yield InboxCleanupWork._schedule(txn, seconds)
    yield txn.commit()
    returnValue(wp)
