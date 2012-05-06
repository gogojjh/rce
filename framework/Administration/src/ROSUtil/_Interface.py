#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#       _Interface.py
#       
#       Copyright 2012 dominique hunziker <dominique.hunziker@gmail.com>
#       
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 2 of the License, or
#       (at your option) any later version.
#       
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#       
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.
#       
#       

# ROS specific imports
import rospy
import genpy

# Python specific imports
from threading import Event, Lock
from struct import error as StructError

# Custom imports
import settings
from Exceptions import InternalError, SerializationError
from ContentDefinition import STRUCT_I, LEN_I
from MiscUtility import generateID

class InterfaceBase(object):
    """ Base class which represents an interface for a node.
    """
    # TODO: Use slots yes or no?
    # __slots__ = ['interfaceName', '_manager', 'ready']

    def __init__(self, interfaceName):
        """ Initialize the Interface instance.

            @param interfaceName:   ROS name of the interface.
            @type  interfaceName:   str

        """
        self.interfaceName = interfaceName
        self._manager = None
        self.ready = False

    @classmethod
    def deserialize(cls, data):
        """ Deserialize the Interface instance.
        """
        try:
            start = 0
            end = LEN_I
            length, = STRUCT_I.unpack(data[start:end])
            start = end
            end += length
            name = data[start:end]
        except StructError as e:
            raise SerializationError('Could not deserialize Interface: {0}'.format(e))

        return cls(name)

    def registerManager(self, manager):
        """ This method is used to register the manager instance, which is needed
            for callback functionalities.

            @param manager: Manager instance which should be registered.
            @type  manager: Manager

            @raise:     InternalError if there is already a manager
                        registered.
        """
        if self._manager:
            raise InternalError('There is already a manager registered.')

        self._manager = manager

    def _start(self):
        """ This method should be overwritten to implement necessary start
            up procedures.
        """
        pass

    def start(self):
        """ This method is used to setup the interface.

            Don't overwrite this method; instead overwrite the method _start.

            @raise:     InternalError if the interface can not be started.
        """
        if not self._manager:
            raise InternalError('Can not start an interface without a registered manager.')

        if self.ready:
            return

        self._start()

        self._manager.registerInterface(self)
        self.ready = True

    def _send(self, msg):
        """ This method should be overwritten to implement the send functionality.
        """
        raise InternalError('Interface does not support sending of a message.')

    def send(self, msg):
        """ This method is used to send a message to a node.

            Don't overwrite this method; instead overwrite the method _send.
            If the interface does not overwrite the method _send, it is assumed that
            the interface does not support this action and an InternalError is
            raised.

            @param msg:     Message which should be sent in serialized form.
            @type  msg:     str

            @return:    ID which can be used to match a received message to a
                        sent one or if this is not possible None.
            @rtype:     str
        """
        if self.ready:
            raise InternalError('Interface is not ready to send a message.')

        return self._send(msg)

    def _receive(self, taskID):
        """ This method should be overwritten to implement the receive functionality.
        """
        raise InternalError('Interface does not support receiving of a message.')

    def receive(self, taskID):
        """ This method is used to receive a message from a node.

            Don't overwrite this method; instead overwrite the method _receive.
            If the interface does not overwrite the method _receive, it is assumed
            that the interface does not support this action and an InternalError is
            raised.

            @param taskID:  ID which can be used to match a sent message to received
                            one or if this is not possible this argument will be
                            ignored.
            @type  taskID:  str

            @return:    Received message in serialized form.
            @rtype:     str
        """
        if self.ready:
            raise InternalError('Interface is not ready to receive a message.')

        return self._receive(taskID)

    def _stop(self):
        """ This method should be overwritten to implement necessary tear
            down procedures.
        """
        pass

    def stop(self):
        """ This method is used to stop the interface.

            Don't overwrite this method; instead overwrite the method _stop.
        """
        if not self.ready:
            return

        self._stop()

        self.ready = False
        self._manager.unregisterInterface(self)

class ServiceInterface(InterfaceBase):
    """ Represents a service interface for a node.
    """
    class ServiceTask(object):
        """ Data container for a single Task, which consists of a
            request and its corresponding result.
        """
        __slots__ = ['_completed', '_error', '_srv', '_msg', '_id']

        def __init__(self, srv, msg):
            """ Initialize the Task.

                @param srv:     ServiceInterface which uses this task.
                @type  srv:     ServiceInterface

                @param msg:     Message which should be sent as a request
                                in its serialized form.
                @type  msg:     str
            """
            self._id = None
            self._srv = srv
            self._msg = msg

            self._completed = False
            self._error = False

            self._hasID = Event()
            self._signalCompletion = False

        def setID(self, taskID):
            """ Set the ID of this task.

                @param taskID:  ID which identifies this Task.
                @type  taskID:  str

                @raise:    InternalError if Task already has an ID.
            """
            if self._hasID.isSet():
                raise InternalError('Task already has an ID.')

            self._id = taskID
            self._hasID.set()

        def run(self):
            """ This method contains the "task" and is the part which should
                be executed in a separate thread.
            """
            msg = rospy.AnyMsg()
            msg._buff = self._msg

            try:
                rospy.wait_for_service(self._srv.interfaceName, timeout=settings.WAIT_FOR_SERVICE_TIMEOUT)
                serviceFunc = rospy.ServiceProxy(self._srv.interfaceName, self._srv.srvCls)
                response = serviceFunc(msg)
            except rospy.ROSInterruptException:
                return
            except Exception as e:
                self._msg = str(e)
                self._error = True
            else:
                self._msg = response._buff
                self._completed = True

            self._hasID.wait(1)

            if self._signalCompleted:
                self._srv._signalCompletion(self._id)

        def getResult(self):
            """ Get the result of this task.

                - Returns serialized message which was received as response
                  to the sent request.
                - Returns None if the task has not yet been completed.
                - Raises an InternalError if there was an error in the run()
                  method.
            """
            self._signalCompleted = True

            if self._completed:
                return self._msg
            elif self._error:
                # TODO: Good idea to raise an error here?
                raise InternalError(self._msg)
            else:
                return None

    # TODO: Use slots yes or no?
    # __slots__ = ['srvCls', '_tasks', '_tasksLock']

    def __init__(self, cls, interfaceName):
        super(ServiceInterface, self).__init__(interfaceName)

        try:
            self.srvCls = genpy.message.get_service_class(cls)
        except (ValueError):
            raise SerializationError('Could not load Service class.')

        self.srvCls._request_class = rospy.AnyMsg
        self.srvCls._response_class = rospy.AnyMsg

        self._tasks = {}
        self._tasksLock = Lock()

    @classmethod
    def deserialize(cls, data):
        try:
            start = 0
            end = LEN_I
            length, = STRUCT_I.unpack(data[start:end])
            start = end
            end += length
            name = data[start:end]

            start = end
            end += LEN_I
            length, = STRUCT_I.unpack(data[start:end])
            start = end
            end += length
            cls = data[start:end]
        except StructError as e:
            raise SerializationError('Could not deserialize Interface: {0}'.format(e))

        return cls(cls, name)

    __init__.__doc__ = InterfaceBase.__init__.__doc__
    deserialize.__func__.__doc__ = InterfaceBase.deserialize.__func__.__doc__

    def _send(self, msg):
        task = ServiceInterface.ServiceTask(self, msg)
        self._manager.runTaskInSeparateThread(task.run)

        while True:
            uid = generateID()

            with self._tasksLock:
                if uid not in self._tasks.keys():
                    self._tasks[uid] = task
                    break

        task.setID(uid)

        return uid

    def _receive(self, taskID):
        with self._tasksLock:
            try:
                msg = self._tasks[taskID].getResult()
            except KeyError:
                raise InternalError('Invalid taskID used.')

        if not msg:
            # There is no response yet
            # TODO: Signal back, that there was no response, or identify empty msg with no response
            msg = ''

        return msg

    def _signalCompletion(self, taskID):
        """ Callback function for ServiceTask instances to signal
            completion of the task (only on demand executed).

            @param taskID:  ID which identifies the completed task.
            @type  taskID:  str
        """
        # TODO: Add / change method send for manager...
        # TODO: What has to be sent to signal completion of task?
        pass

class PublisherInterface(InterfaceBase):
    """ Represents a publisher interface for a node.
    """
    # TODO: Use slots yes or no?
    # __slots__ = ['_publisher']

    def _start(self):
        self._publisher = rospy.Publisher(self.interfaceName, rospy.AnyMsg, latch=True)

    def _send(self, msgData):
        msg = rospy.AnyMsg()
        msg._buff = msgData

        try:
            self._publisher.publish(msg)
        except rospy.ROSInterruptException:
            pass
        except rospy.ROSSerializationException:
            raise InternalError('Message could not be serialized by ROS.')

        return ''

    def _stop(self):
        self._publisher.unregister()
        self._publisher = None

class SubscriberInterface(InterfaceBase):
    """ Represents a subscriber interface for a node.
    """
    # TODO: Use slots yes or no?
    # __slots__ = ['_subscriber', '_lastMsg', '_msgLock']

    def __init__(self, interfaceName):
        super(SubscriberInterface, self).__init__(interfaceName)

        self._lastMsg = None
        self._msgLock = Lock()

    __init__.__doc__ = InterfaceBase.__init__.__doc__

    def _start(self):
        self._subscriber = rospy.Subscriber(self.interfaceName, rospy.AnyMsg, self._callbackFunc)

    def _receive(self, taskID):
        with self._msgLock:
            return self._lastMsg

    def _stop(self):
        self._subscriber.unregister()
        self._subscriber = None

    def _callbackFunc(self, msg):
        with self._msgLock:
            self._lastMsg = msg._buff
