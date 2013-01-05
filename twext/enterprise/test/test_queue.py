##
# Copyright (c) 2012 Apple Inc. All rights reserved.
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
Tests for L{twext.enterprise.queue}.
"""

import datetime

# TODO: There should be a store-building utility within twext.enterprise.

from twisted.protocols.amp import Command
from txdav.common.datastore.test.util import buildStore

from twext.enterprise.dal.syntax import SchemaSyntax, Select
from twext.enterprise.dal.record import fromTable
from twext.enterprise.dal.test.test_parseschema import SchemaTestHelper

from twext.enterprise.queue import inTransaction, PeerConnectionPool, WorkItem

from twisted.trial.unittest import TestCase
from twisted.internet.defer import (
    Deferred, inlineCallbacks, gatherResults, passthru
)

from twisted.application.service import Service, MultiService

from twext.enterprise.queue import (
    ImmediatePerformer, _IWorkPerformer, WorkerConnectionPool, SchemaAMP,
    TableSyntaxByName
)

from twext.enterprise.dal.record import Record

from twext.enterprise.queue import ConnectionFromPeerNode
from zope.interface.verify import verifyObject
from twisted.test.proto_helpers import StringTransport

class UtilityTests(TestCase):
    """
    Tests for supporting utilities.
    """

    def test_inTransactionSuccess(self):
        """
        L{inTransaction} invokes its C{transactionCreator} argument, and then
        returns a L{Deferred} which fires with the result of its C{operation}
        argument when it succeeds.
        """
        class faketxn(object):
            def __init__(self):
                self.commits = []
                self.aborts = []
            def commit(self):
                self.commits.append(Deferred())
                return self.commits[-1]
            def abort(self):
                self.aborts.append(Deferred())
                return self.aborts[-1]

        createdTxns = []
        def createTxn():
            createdTxns.append(faketxn())
            return createdTxns[-1]
        dfrs = []
        def operation(t):
            self.assertIdentical(t, createdTxns[-1])
            dfrs.append(Deferred())
            return dfrs[-1]
        d = inTransaction(createTxn, operation)
        x = []
        d.addCallback(x.append)
        self.assertEquals(x, [])
        self.assertEquals(len(dfrs), 1)
        dfrs[0].callback(35)
        # Commit in progress, so still no result...
        self.assertEquals(x, [])
        createdTxns[0].commits[0].callback(42)
        # Committed, everything's done.
        self.assertEquals(x, [35])



class SimpleSchemaHelper(SchemaTestHelper):
    def id(self):
        return 'worker'

SQL = passthru

schemaText = SQL("""
    create table DUMMY_WORK_ITEM (WORK_ID integer primary key,
                                  NOT_BEFORE timestamp,
                                  A integer, B integer);
    create table DUMMY_WORK_DONE (WORK_ID integer, A_PLUS_B integer);
""")

schema = SchemaSyntax(SimpleSchemaHelper().schemaFromString(schemaText))

dropSQL = ["drop table {name}".format(name=table.model.name)
           for table in schema]


class DummyWorkDone(Record, fromTable(schema.DUMMY_WORK_DONE)):
    """
    Work result.
    """



class DummyWorkItem(WorkItem, fromTable(schema.DUMMY_WORK_ITEM)):
    """
    Sample L{WorkItem} subclass that adds two integers together and stores them
    in another table.
    """

    def doWork(self):
        return DummyWorkDone.create(self.transaction, workID=self.workID,
                                    aPlusB=self.a + self.b)



class SchemaAMPTests(TestCase):
    """
    Tests for L{SchemaAMP} faithfully relaying tables across the wire.
    """

    def test_sendTableWithName(self):
        """
        You can send a reference to a table through a L{SchemaAMP} via
        L{TableSyntaxByName}.
        """
        client = SchemaAMP(schema)
        class SampleCommand(Command):
            arguments = [('table', TableSyntaxByName())]
        class Receiver(SchemaAMP):
            @SampleCommand.responder
            def gotIt(self, table):
                self.it = table
                return {}
        server = Receiver(schema)
        clientT = StringTransport()
        serverT = StringTransport()
        client.makeConnection(clientT)
        server.makeConnection(serverT)
        client.callRemote(SampleCommand, table=schema.DUMMY_WORK_ITEM)
        server.dataReceived(clientT.io.getvalue())
        self.assertEqual(server.it, schema.DUMMY_WORK_ITEM)




class WorkItemTests(TestCase):
    """
    A L{WorkItem} is an item of work that can be executed.
    """

    def test_forTable(self):
        """
        L{WorkItem.forTable} returns L{WorkItem} subclasses mapped to the given
        table.
        """
        self.assertIdentical(WorkItem.forTable(schema.DUMMY_WORK_ITEM),
                             DummyWorkItem)



class WorkerConnectionPoolTests(TestCase):
    """
    A L{WorkerConnectionPool} is responsible for managing, in a node's
    controller (master) process, the collection of worker (slave) processes
    that are capable of executing queue work.
    """



class PeerConnectionPoolUnitTests(TestCase):
    """
    L{PeerConnectionPool} has many internal components.
    """
    def setUp(self):
        """
        Create a L{PeerConnectionPool} that is just initialized enough.
        """
        self.pcp = PeerConnectionPool(None, None, 4321, schema)


    def checkPerformer(self, cls):
        """
        Verify that the performer returned by
        L{PeerConnectionPool.choosePerformer}.
        """
        performer = self.pcp.choosePerformer()
        self.failUnlessIsInstance(performer, cls)
        verifyObject(_IWorkPerformer, performer)


    def test_choosingPerformerWhenNoPeersAndNoWorkers(self):
        """
        If L{PeerConnectionPool.choosePerformer} is invoked when no workers
        have spawned and no peers have established connections (either incoming
        or outgoing), then it chooses an implementation of C{performWork} that
        simply executes the work locally.
        """
        self.checkPerformer(ImmediatePerformer)


    def test_choosingPerformerWithLocalCapacity(self):
        """
        If L{PeerConnectionPool.choosePerformer} is invoked when some workers
        have spawned, then it should choose the worker pool as the local
        performer.
        """
        # Give it some local capacity.
        wlf = self.pcp.workerListenerFactory()
        proto = wlf.buildProtocol(None)
        proto.makeConnection(StringTransport())
        # Sanity check.
        self.assertEqual(len(self.pcp.workerPool.workers), 1)
        self.assertEqual(self.pcp.workerPool.hasAvailableCapacity(), True)
        # Now it has some capacity.
        self.checkPerformer(WorkerConnectionPool)


    def test_choosingPerformerFromNetwork(self):
        """
        If L{PeerConnectionPool.choosePerformer} is invoked when no workers
        have spawned but some peers have connected, then it should choose a
        connection from the network to perform it.
        """
        peer = PeerConnectionPool(None, None, 4322, schema)
        local = self.pcp.peerFactory().buildProtocol(None)
        remote = peer.peerFactory().buildProtocol(None)
        connection = Connection(local, remote)
        connection.start()
        self.checkPerformer(ConnectionFromPeerNode)


    def test_performingWorkOnNetwork(self):
        """
        The L{PerformWork} command will get relayed to the remote peer
        controller.
        """
        peer = PeerConnectionPool(None, None, 4322, schema)
        local = self.pcp.peerFactory().buildProtocol(None)
        remote = peer.peerFactory().buildProtocol(None)
        connection = Connection(local, remote)
        connection.start()
        d = Deferred()
        class DummyPerformer(object):
            def performWork(self, table, workID):
                self.table = table
                self.workID = workID
                return d
        # Doing real database I/O in this test would be tedious so fake the
        # first method in the call stack which actually talks to the DB.
        dummy = DummyPerformer()
        def chooseDummy(onlyLocally=False):
            return dummy
        peer.choosePerformer = chooseDummy
        performed = local.performWork(schema.DUMMY_WORK_ITEM, 7384)
        performResult = []
        performed.addCallback(performResult.append)
        # Sanity check.
        self.assertEquals(performResult, [])
        connection.flush()
        self.assertEquals(dummy.table, schema.DUMMY_WORK_ITEM)
        self.assertEquals(dummy.workID, 7384)
        self.assertEquals(performResult, [])
        d.callback(128374)
        connection.flush()
        self.assertEquals(performResult, [None])



class HalfConnection(object):
    def __init__(self, protocol):
        self.protocol = protocol
        self.transport = StringTransport()


    def start(self):
        """
        Hook up the protocol and the transport.
        """
        self.protocol.makeConnection(self.transport)


    def extract(self):
        """
        Extract the data currently present in this protocol's output buffer.
        """
        io = self.transport.io
        value = io.getvalue()
        io.seek(0)
        io.truncate()
        return value


    def deliver(self, data):
        """
        Deliver the given data to this L{HalfConnection}'s protocol's
        C{dataReceived} method.

        @return: a boolean indicating whether any data was delivered.
        @rtype: L{bool}
        """
        if data:
            self.protocol.dataReceived(data)
            return True
        return False



class Connection(object):

    def __init__(self, local, remote):
        """
        Connect two protocol instances to each other via string transports.
        """
        self.receiver = HalfConnection(local)
        self.sender = HalfConnection(remote)


    def start(self):
        """
        Start up the connection.
        """
        self.sender.start()
        self.receiver.start()


    def pump(self):
        """
        Relay data in one direction between the two connections.
        """
        result = self.receiver.deliver(self.sender.extract())
        self.receiver, self.sender = self.sender, self.receiver
        return result

    def flush(self, turns=10):
        """
        Keep relaying data until there's no more.
        """
        for x in range(turns):
            if not (self.pump() or self.pump()):
                return



class PeerConnectionPoolIntegrationTests(TestCase):
    """
    L{PeerConnectionPool} is the service responsible for coordinating
    eventually-consistent task queuing within a cluster.
    """

    @inlineCallbacks
    def setUp(self):
        """
        L{PeerConnectionPool} requires access to a database and the reactor.
        """
        self.store = yield buildStore(self, None)
        def doit(txn):
            return txn.execSQL(schemaText)
        yield inTransaction(lambda: self.store.newTransaction("bonus schema"),
                            doit)
        def deschema():
            @inlineCallbacks
            def deletestuff(txn):
                for stmt in dropSQL:
                    yield txn.execSQL(stmt)
            return inTransaction(self.store.newTransaction, deletestuff)
        self.addCleanup(deschema)

        from twisted.internet import reactor
        self.node1 = PeerConnectionPool(
            reactor, self.store.newTransaction, 0, schema)
        self.node2 = PeerConnectionPool(
            reactor, self.store.newTransaction, 0, schema)

        class FireMeService(Service, object):
            def __init__(self, d):
                super(FireMeService, self).__init__()
                self.d = d
            def startService(self):
                self.d.callback(None)
        d1 = Deferred()
        d2 = Deferred()
        FireMeService(d1).setServiceParent(self.node1)
        FireMeService(d2).setServiceParent(self.node2)
        ms = MultiService()
        self.node1.setServiceParent(ms)
        self.node2.setServiceParent(ms)
        ms.startService()
        self.addCleanup(ms.stopService)
        yield gatherResults([d1, d2])
        self.store.queuer = self.node1


    def test_currentNodeInfo(self):
        """
        There will be two C{NODE_INFO} rows in the database, retrievable as two
        L{NodeInfo} objects, once both nodes have started up.
        """
        @inlineCallbacks
        def check(txn):
            self.assertEquals(len((yield self.node1.activeNodes(txn))), 2)
            self.assertEquals(len((yield self.node2.activeNodes(txn))), 2)
        return inTransaction(self.store.newTransaction, check)


    @inlineCallbacks
    def test_enqueueHappyPath(self):
        """
        When a L{WorkItem} is scheduled for execution via
        L{PeerConnectionPool.enqueueWork} its C{doWork} method will be invoked
        by the time the L{Deferred} returned from the resulting
        L{WorkProposal}'s C{whenExecuted} method has fired.
        """
        # TODO: this exact test should run against NullQueuer as well.
        def operation(txn):
            # TODO: how does 'enqueue' get associated with the transaction? This
            # is not the fact with a raw t.w.enterprise transaction.  Should
            # probably do something with components.
            return txn.enqueue(DummyWorkItem, a=3, b=4, workID=4321,
                               notBefore=datetime.datetime.now())
        result = yield inTransaction(self.store.newTransaction, operation)
        # Wait for it to be executed.  Hopefully this does not time out :-\.
        yield result.whenExecuted()
        def op2(txn):
            return Select([schema.DUMMY_WORK_DONE.WORK_ID,
                           schema.DUMMY_WORK_DONE.A_PLUS_B],
                           From=schema.DUMMY_WORK_DONE).on(txn)
        rows = yield inTransaction(self.store.newTransaction, op2)
        self.assertEquals(rows, [[4321, 7]])


